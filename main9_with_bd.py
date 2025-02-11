import json
from typing import List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, delete, func, select, Text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from sqlalchemy.ext.asyncio import AsyncAttrs
from datetime import datetime, timezone
import random
import asyncio

# Database setup
DATABASE_URL = "postgresql+asyncpg://admin:admin@db:5432/test_new"
#DATABASE_URL = "postgresql+asyncpg://admin:12439862@localhost/test_new"
engine = create_async_engine(DATABASE_URL)
async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

Base = declarative_base()

class User(Base, AsyncAttrs):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, index=True)
    score = Column(Integer, default=0)
    answers = relationship("Answer", back_populates="user", cascade="all, delete-orphan")

class Question(Base, AsyncAttrs):
    __tablename__ = "questions"
    id = Column(Integer, primary_key=True)
    section = Column(String)
    text = Column(Text)
    #true_answer = Column(Text)

    #question_image = Column(Text)
    #answer_image = Column(Text)

class Answer(Base, AsyncAttrs):
    __tablename__ = "answers"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    question_id = Column(Integer, ForeignKey("questions.id"))
    answer_text = Column(String)
    answered_at = Column(String)
    user = relationship("User", back_populates="answers")
    question = relationship("Question")

    #DateTime, default=(datetime.now()).strftime("%H:%M:%S")

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# FastAPI app setup
app = FastAPI()

# Глобальные переменные состояния игры
current_section_index = 0
current_question = None
answered_users = set()
game_started = False
game_over = False
spectator_display_mode = "question"
sections = []
data = {}

active_players = {}       # {id: {'ws': WebSocket, 'name': str}}
active_spectators = {}    # {id: WebSocket}
#player_answers = {}       # {имя: [{'question': str, 'answer': str}]}
player_scores = {}        # {имя: int} - система рейтинга

# Dependency to get DB session
async def get_db():
    async with async_session() as session:
        yield session


@app.on_event("startup")
async def on_startup():
    await init_db()


html_player = """
<!DOCTYPE html>
<html>
    <head>
        <title>Викторина</title>
        <style>
            #notification {
                color: green;
                margin: 10px 0;
                display: none;
            }
        </style>
    </head>
    <body>
        <div id="nameForm" style="display: none;">
            <h1>Введите ваше имя:</h1>
            <input type="text" id="nameInput" placeholder="Ваше имя">
            <button onclick="submitName()">Подтвердить</button>
        </div>
        <div id="gameScreen" style="display: none;">
            <h1 id="question">Ждите начала игры...</h1>
            <div id="notification"></div>
            <input type="text" id="answerInput" placeholder="Ваш ответ" disabled>
            <button id="answerButton" onclick="sendAnswer()" disabled>Ответить</button>
        </div>
        <script>
            var ws;
            var playerName = localStorage.getItem("playerName");

            function showNotification(text, isError = false) {
                const notification = document.getElementById("notification");
                notification.style.color = isError ? "red" : "green";
                notification.textContent = text;
                notification.style.display = "block";
                setTimeout(() => {
                    notification.style.display = "none";
                }, 3000);
            }

            function updateUI(questionText) {
                const isGameActive = !questionText.includes("Игра завершена");
                const isQuestionActive = isGameActive && questionText.length > 0 
                    && !questionText.includes("Ожидайте вопрос");

                document.getElementById("answerInput").disabled = !isQuestionActive;
                document.getElementById("answerButton").disabled = !isQuestionActive;
                
                if (!isQuestionActive) {
                    document.getElementById("answerInput").value = "";
                }
            }

            function init() {
                if (playerName) {
                    connectToGame(playerName);
                } else {
                    document.getElementById("nameForm").style.display = "block";
                    document.getElementById("gameScreen").style.display = "none";
                }
            }

            function submitName() {
                const name = document.getElementById("nameInput").value;
                if (name.trim() === "") {
                    showNotification("Введите имя!", true);
                    return;
                }
                localStorage.setItem("playerName", name);
                connectToGame(name);
            }

            function connectToGame(name) {
                document.getElementById("nameForm").style.display = "none";
                document.getElementById("gameScreen").style.display = "block";
                
                ws = new WebSocket("ws://localhost:8000/ws/player");
                
                ws.onopen = () => {
                    ws.send(JSON.stringify({ type: "set_name", name: name }));
                };

                ws.onmessage = (event) => {
                    const data = event.data;
                    if (data === "clear_storage") {
                        localStorage.clear();
                        location.reload();
                        return;
                    }
                    document.getElementById("question").textContent = data;
                    updateUI(data);
                    
                    if (data === "Игра начата! Ожидайте первый вопрос.") {
                        showNotification("Игра началась! Ждите первый вопрос");
                    }
                };
            }

            function sendAnswer() {
                const answer = document.getElementById("answerInput").value;
                if (answer.trim() === "") {
                    showNotification("Введите ответ!", true);
                    return;
                }
                
                ws.send(JSON.stringify({ type: "answer", answer: answer }));
                showNotification("Ответ отправлен! Ждите результат");
                document.getElementById("answerInput").value = "";
                document.getElementById("answerInput").disabled = true;
                document.getElementById("answerButton").disabled = true;
            }

            init();
        </script>
    </body>
</html>
"""

html_spectator = """
<!DOCTYPE html>
<html>
    <head>
        <title>Зритель</title>
        <style>
            #rating-table {
                width: 100%;
                border-collapse: collapse;
                margin: 20px 0;
                display: none;
            }
            #rating-table th, #rating-table td {
                border: 1px solid #ddd;
                padding: 8px;
                text-align: left;
            }
            #rating-table th {
                background-color: #f2f2f2;
            }
            #question {
                margin: 20px 0;
            }
        </style>
    </head>
    <body>
        <table id="rating-table">
            <thead>
                <tr>
                    <th>Место</th>
                    <th>Игрок</th>
                    <th>Баллы</th>
                </tr>
            </thead>
            <tbody></tbody>
        </table>
        <h1 id="question">Ждите начала игры...</h1>
        <script>
            const ws = new WebSocket("ws://localhost:8000/ws/spectator");
            
            function updateDisplay(data) {
                if (data.type === 'rating') {
                    document.getElementById('question').style.display = 'none';
                    const table = document.getElementById('rating-table');
                    table.style.display = 'table';
                    
                    const tbody = table.querySelector('tbody');
                    tbody.innerHTML = '';
                    
                    data.players.forEach((player, index) => {
                        const row = document.createElement('tr');
                        row.innerHTML = `
                            <td>${index + 1}</td>
                            <td>${player.name}</td>
                            <td>${player.score}</td>
                        `;
                        tbody.appendChild(row);
                    });
                } else {
                    document.getElementById('rating-table').style.display = 'none';
                    document.getElementById('question').style.display = 'block';
                    document.getElementById('question').textContent = data.content;
                }
            }

            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                updateDisplay(data);
            };
        </script>
    </body>
</html>
"""

admin_html = """
<!DOCTYPE html>
<html>
    <head>
        <title>Админ</title>
        <style>
            .container {
                display: grid;
                grid-template-columns: 1fr 2fr;
                gap: 20px;
            }
            table {
                border-collapse: collapse;
                width: 100%;
            }
            th, td {
                border: 1px solid #ddd;
                padding: 8px;
                text-align: left;
            }
            th {
                background-color: #f2f2f2;
            }
            .score-controls {
                margin-left: 10px;
            }
            button {
                cursor: pointer;
            }
            .new-buttons {
                margin-top: 10px;
                display: flex;
                gap: 10px;
            }
        </style>
    </head>
    <body>
        <h1>Админ-панель</h1>
        <div class="container">
            <div>
                <button onclick="startGame()">Начать игру</button>
                <button onclick="nextQuestion()">Следующий вопрос</button>
                <button onclick="stopGame()">Закончить игру</button>
                
                <div class="new-buttons">
                    <button onclick="showRating()">Показать рейтинг</button>
                    <button onclick="showQuestion()">Показать вопрос</button>
                </div>

                <h3>Активные участники:</h3>
                <ul id="players"></ul>
            </div>
            
            <div>
                <h3>История ответов:</h3>
                <table id="answersTable">
                    <thead>
                        <tr>
                            <th>Игрок</th>
                            <th>Вопрос</th>
                            <th>Ответ</th>
                            <th>Время</th>
                        </tr>
                    </thead>
                    <tbody></tbody>
                </table>
            </div>
        </div>

        <script>
            function updateData() {
                fetch("/admin/players")
                    .then(r => r.json())
                    .then(data => {
                        const list = document.getElementById("players");
                        list.innerHTML = data.players.map(p => `
                            <li>
                                ${p.name} | Баллы: ${p.score}
                                <div class="score-controls">
                                    <button onclick="addPoint('${p.name}')">+</button>
                                    <button onclick="removePoint('${p.name}')">-</button>
                                </div>
                            </li>
                        `).join('');
                    });
                
                fetch("/admin/answers")
                    .then(r => r.json())
                    .then(data => {
                        const tbody = document.querySelector('#answersTable tbody');
                        tbody.innerHTML = '';
                        
                        data.answers.forEach(item => {
                            const row = document.createElement('tr');
                            row.innerHTML = `
                                <td>${item.user}</td>
                                <td>${item.question}</td>
                                <td>${item.answer}</td>
                                <td>${item.time}</td>
                            `;
                            tbody.appendChild(row);
                        });
                    });
            }
            
            setInterval(updateData, 1500);
            updateData();

            function addPoint(playerName) {
                fetch(`/admin/add_point/${encodeURIComponent(playerName)}`, { method: "POST" });
            }

            function removePoint(playerName) {
                fetch(`/admin/remove_point/${encodeURIComponent(playerName)}`, { method: "POST" });
            }

            function startGame() { fetch("/admin/start", { method: "POST" }); }
            function nextQuestion() { fetch("/admin/next", { method: "POST" }); }
            function stopGame() { fetch("/admin/stop", { method: "POST" }); }
            function showRating() { fetch("/admin/show_rating", { method: "POST" }); }
            function showQuestion() { fetch("/admin/show_question", { method: "POST" }); }
        </script>
    </body>
</html>
"""

@app.get("/")
async def get_player():
    return HTMLResponse(html_player)

@app.get("/spectator")
async def get_spectator():
    return HTMLResponse(html_spectator)

@app.get("/admin")
async def admin_panel():
    return HTMLResponse(admin_html)

# Модифицированные эндпоинты
@app.post("/admin/add_point/{player_name}")
async def add_point(player_name: str, db: AsyncSession = Depends(get_db)):
    user = await db.execute(select(User).filter(User.name == player_name))
    user = user.scalar()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.score += 1
    await db.commit()
    return {"message": "OK"}

@app.post("/admin/remove_point/{player_name}")
async def remove_point(player_name: str, db: AsyncSession = Depends(get_db)):
    user = await db.execute(select(User).filter(User.name == player_name))
    user = user.scalar()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.score = max(0, user.score - 1)
    await db.commit()
    return {"message": "OK"}

@app.get("/admin/players")
async def get_active_players(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User))
    users = result.scalars().all()
    return {"players": [{"name": u.name, "score": u.score} for u in users]}

@app.post("/admin/start")
async def start_game(db: AsyncSession = Depends(get_db)):
    global game_started, game_over, data, sections, current_section_index, current_question, answered_users
    
    # Очищаем предыдущие ответы и пользователей
    #await db.execute(delete(Answer))
    #await db.execute(delete(User))
    #await db.commit()
    
    # Загружаем вопросы из БД
    result = await db.execute(select(Question))
    questions = result.scalars().all()
    
    # Формируем структуру данных
    data.clear()
    for q in questions:
        if q.section not in data:
            data[q.section] = []
        data[q.section].append(q.text)
    
    sections = list(data.keys())
    current_section_index = 0
    current_question = None
    answered_users = set()
    game_started = True
    game_over = False
    await _broadcast("Игра начата! Ожидайте первый вопрос.")
    return {"message": "Игра начата"}

@app.post("/admin/next")
async def next_question():
    global current_section_index, current_question, answered_users, game_over, data
    
    if not game_started or game_over:
        return {"message": "Игра не активна"}
    
    current_section = sections[current_section_index]
    
    if not data[current_section]:
        current_section_index += 1
        if current_section_index >= len(sections):
            game_over = True
            await _broadcast("Игра завершена! Все разделы пройдены.")
            return {"message": "Все вопросы закончены"}
        
        current_section = sections[current_section_index]
        await _broadcast(f"Переход к разделу: {current_section}")
    
    if data[current_section]:
        current_question = random.choice(data[current_section])
        data[current_section].remove(current_question)
        answered_users = set()
        await _broadcast(current_question)
    else:
        await _broadcast("В этом разделе больше нет вопросов")
    
    return {"message": "OK"}

@app.get("/admin/answers")
async def get_answers(db: AsyncSession = Depends(get_db)):
    stmt = select(
        User.name.label('user_name'),
        Question.text.label('question_text'),
        Answer.answer_text,
        Answer.answered_at
    ).select_from(Answer).join(User).join(Question)
    
    result = await db.execute(stmt)
    answers = result.all()
    
    formatted_answers = []
    for answer in answers:
        formatted_answers.append({
            "user": answer.user_name,
            "question": answer.question_text,
            "answer": answer.answer_text,
            "time": answer.answered_at
        })
    
    return {"answers": formatted_answers}

@app.post("/admin/show_rating")
async def show_rating():
    global spectator_display_mode
    spectator_display_mode = "rating"
    await _broadcast_spectators()
    return {"message": "Рейтинг показан"}

@app.post("/admin/show_question")
async def show_question():
    global spectator_display_mode
    spectator_display_mode = "question"
    await _broadcast_spectators()
    return {"message": "Вопрос показан"}

@app.post("/admin/stop")
async def stop_game():
    global game_started, game_over, player_answers
    game_started = False
    game_over = True
    player_answers = {}

    await _broadcast("clear_storage")
    await _broadcast("Игра завершена администратором.")
    return {"message": "Игра остановлена"}

# Новые эндпоинты для управления вопросами
@app.post("/admin/questions")
async def add_question(questions: List[dict], db: AsyncSession = Depends(get_db)):
    #question = Question(section=section, text=text)
    #db.add(question)
    #await db.commit()

    for question in questions:
            new_question = Question(
                text=question.get("text"),
                section=question.get("section"),
                #answer=question.get("answer"),
                
                #question_image=question.get("question_image"),
                #answer_image=question.get("answer_image")

            )
            db.add(new_question)
    await db.commit()

    return {"message": "Question added"}

@app.get("/admin/questions")
async def get_questions(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Question))
    questions = result.scalars().all()
    return {"questions": [{"id": q.id, "section": q.section, "text": q.text} for q in questions]} #TODO: Добвить картинки

@app.delete("/admin/questions/{question_id}")
async def delete_question(question_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Question).filter(Question.id == question_id))
    question = result.scalar()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    await db.delete(question)
    await db.commit()
    return {"message": "Question deleted"}

@app.post("/admin/end_game")
async def end_game(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(delete(Answer).where(Answer.user_id.in_(select(User.id))))
        # Удаляем всех пользователей из базы данных
        await db.execute(delete(User))
        await db.commit()
        return {"message": "Игра завершена, все пользователи удалены."}
    except Exception as e:
        await db.rollback()  # Откат транзакции в случае ошибки
        raise HTTPException(status_code=500, detail=str(e))

# Модифицированный WebSocket обработчик
@app.websocket("/ws/player")
async def websocket_player(websocket: WebSocket, db: AsyncSession = Depends(get_db)):
    await websocket.accept()
    user_id = id(websocket)
    try:
        data = await websocket.receive_text()
        name = json.loads(data)["name"]
        
        # Check and create user
        result = await db.execute(select(User).filter(User.name == name))
        user = result.scalar()
        if not user:
            user = User(name=name)
            db.add(user)
            await db.commit()
            await db.refresh(user)  # Ensure the user is refreshed to get the ID
        
        active_players[user_id] = {'ws': websocket, 'name': name}
        
        # Send initial message
        initial_message = "Игра завершена" if game_over else "Ждите начала игры" if not game_started else current_question or "Ожидайте вопрос"
        await websocket.send_text(initial_message)

        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            
            if msg['type'] == 'answer':
                # Find question in DB
                result = await db.execute(select(Question).filter(Question.text == current_question))
                question = result.scalar()
                if not question:
                    continue
                
                # Save answer
                answer = Answer(
                    user_id=user.id,  # Ensure user.id is valid
                    question_id=question.id,
                    answer_text=msg['answer'],
                    answered_at=(datetime.now()).strftime("%H:%M:%S")
                )
                db.add(answer)
                await db.commit()
                
                answered_users.add(user_id)

    except WebSocketDisconnect:
        del active_players[user_id]


@app.websocket("/ws/spectator")
async def websocket_spectator(websocket: WebSocket):
    await websocket.accept()
    active_spectators[id(websocket)] = websocket
    try:
        await _broadcast_spectators()
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        del active_spectators[id(websocket)]

async def _broadcast_spectators():
    if spectator_display_mode == "rating":
        # Получаем всех игроков и их баллы из базы данных
        async with async_session() as session:
            result = await session.execute(select(User).order_by(User.score.desc()))
            players = result.scalars().all()
            sorted_players = [{"name": player.name, "score": player.score} for player in players]
        
        message = {
            "type": "rating",
            "players": sorted_players
        }
    else:  # Если режим отображения вопроса
        message = {
            "type": "question",
            "content": current_question or "Ожидайте следующий вопрос..."
        }
    
    for spectator in active_spectators.values():
        try:
            await spectator.send_text(json.dumps(message))
        except:
            continue


async def _broadcast(message: str):
    for player in active_players.values():
        try:
            await player['ws'].send_text(message)
        except:
            continue
    
    await _broadcast_spectators()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
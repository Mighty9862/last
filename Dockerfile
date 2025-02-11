# Используем официальный образ Python
FROM python:latest

# Устанавливаем рабочую директорию
WORKDIR /

# Устанавливаем необходимые утилиты
RUN apt-get update

RUN apt install netcat-traditional

# Копируем файл зависимостей
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt


# Копируем весь проект
COPY . .

# Копируем entrypoint скрипт и делаем его исполняемым
#COPY entrypoint.sh /entrypoint.sh
#RUN chmod +x /entrypoint.sh

# Устанавливаем entrypoint
#ENTRYPOINT ["/entrypoint.sh"]

# Указываем команду для запуска приложения
#CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
CMD ["fastapi", "run", "app/main.py", "--proxy-headers", "--port", "8000", "--host", "0.0.0.0", "--workers", "8"]

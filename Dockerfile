FROM python:3.10

# Устанавливаем рабочую директорию
WORKDIR /service

# Обновляем pip и устанавливаем зависимости
COPY ./requirements.txt .
RUN python -m pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Копируем исходный код приложения
COPY . .

# Устанавливаем переменные окружения
ENV PYTHONUNBUFFERED=1

# Устанавливаем точку входа
ENTRYPOINT ["python", "-m", "app"]
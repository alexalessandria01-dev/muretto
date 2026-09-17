FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir "aiohttp>=3.9"
COPY muretto ./muretto
COPY web ./web
# Hugging Face Spaces espone la 7860; Render passa la porta in $PORT
ENV PORT=7860
EXPOSE 7860
CMD ["sh", "-c", "python -m muretto live --port ${PORT}"]

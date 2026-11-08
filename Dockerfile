FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .
RUN useradd --create-home ocr
USER ocr
EXPOSE 10859
CMD ["ocr-mcp", "--host", "0.0.0.0", "--port", "10859"]

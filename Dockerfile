# Use an official lightweight Python runtime as a parent image
FROM python:3.11-slim

# Set environment variables to prevent Python from writing .pyc files and buffering stdout
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code
COPY . .

# Expose the port that Cloud Run expects (8080 by default)
EXPOSE 8080

# Run the FastAPI application using Uvicorn
CMD ["uvicorn", "main:app", "--host", "[IP_ADDRESS]", "--port", "8080"]
# Use a lightweight Python image
FROM python:3.9-slim

# Set the working directory inside the container
WORKDIR /app

# Copy requirements.txt and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code into the container
COPY . .

# Expose the port that the FastAPI server would run on (optional, if needed for client)
EXPOSE 8001

# Command to run the client script
CMD ["python3", "client.py"]
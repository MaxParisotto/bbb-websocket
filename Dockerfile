# Use a lightweight Python image
FROM python:3.9-slim

# Set the working directory inside the container
WORKDIR /app

# Copy requirements.txt (if you have one) and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code into the container
COPY . .

# Expose the port that FastAPI will run on
EXPOSE 8001

# Command to run the FastAPI server
CMD ["uvicorn", "beaglebone_server:app", "--host", "0.0.0.0", "--port", "8001"]
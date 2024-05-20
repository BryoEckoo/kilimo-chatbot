# Use a base image with Python installed
FROM docker.io/python:3.11

# Set the working directory in the container
WORKDIR /app

# Copy the customer's project
COPY . <PROJECT>

# This should set up a local virtual environment for the customer's project to
# run in with some dependencies pre-installed
COPY setup.sh .  
RUN chmod +x ./setup.sh

# We use a special entrypoint script to setup the customer's app environment
# and run it using our previously initialized virtual environment
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

RUN ./setup.sh

# Set the entrypoint command
ENTRYPOINT ["./entrypoint.sh"]
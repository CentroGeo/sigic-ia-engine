# Run composer
docker-compose up --build -d


# Run one a one
# run apps background
docker-compose up --build

# run apps
docker-compose up --build -d


# LOGS
docker logs -f flask-app 
docker logs -f llm-lb
docker exec -it redis redis-cli

# stop apps
docker-compose down



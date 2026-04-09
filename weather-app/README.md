# Local Start

1. cd weather-app/backend; python main.py
2. cd weather-app/frontend; npm run dev

# Azure Start

1. docker login capstonehvacapp.azurecr.io
2. docker-compose build
3. docker push capstonehvacapp.azurecr.io/weather-backend:latest
4. docker push capstonehvacapp.azurecr.io/weather-frontend:latest

BACKEND URL
https://portal.azure.com/?Microsoft_Azure_Education_correlationId=6c4a1371-88e6-4aee-9b6b-889aec97ca87&Microsoft_Azure_Education_newA4E=true&Microsoft_Azure_Education_asoSubGuid=2ce81c61-4e49-45eb-a958-32cffc7978c8#@torontomu.ca/resource/subscriptions/2ce81c61-4e49-45eb-a958-32cffc7978c8/resourceGroups/capstone-am01/providers/Microsoft.App/containerapps/weather-backend/containerapp

FRONTEND URL
https://portal.azure.com/?Microsoft_Azure_Education_correlationId=6c4a1371-88e6-4aee-9b6b-889aec97ca87&Microsoft_Azure_Education_newA4E=true&Microsoft_Azure_Education_asoSubGuid=2ce81c61-4e49-45eb-a958-32cffc7978c8#@torontomu.ca/resource/subscriptions/2ce81c61-4e49-45eb-a958-32cffc7978c8/resourceGroups/capstone-am01/providers/Microsoft.App/containerapps/weather-frontend/containerapp
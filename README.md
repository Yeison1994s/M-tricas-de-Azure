# Azure DevOps Webhook Metrics API

API desarrollada en Python (FastAPI) para recibir eventos (webhooks) de Azure DevOps, procesarlos y almacenarlos en una base de datos PostgreSQL (Neon) para su análisis posterior.

# Arquitectura
FastAPI → API REST para recibir webhooks
Azure DevOps → Fuente de eventos (Pull Requests, etc.)
ngrok → Exposición pública del servidor local
Neon (PostgreSQL Serverless) → Almacenamiento de datos
uvicorn → Servidor ASGI

# Flujo de funcionamiento
Azure DevOps envía eventos (webhooks)
ngrok expone el endpoint local
FastAPI recibe la solicitud
Se procesa el payload
Se guarda en PostgreSQL (Neon)
🛠️ Instalación
1. Clonar el repositorio
git clone https://github.com/Yeison1994s/M-tricas-de-Azure.git
cd M-tricas-de-Azure
2. Crear entorno virtual
python3 -m venv venv
source venv/bin/activate
3. Instalar dependencias
pip install -r requirements.txt

# Configuración
Variables de entorno

Configura la conexión a Neon:

export DATABASE_URL="postgresql://USER:PASSWORD@HOST/DBNAME?sslmode=require"

Ejemplo real de Neon:

postgresql://user:password@ep-xxxxx.us-east-1.aws.neon.tech/dbname?sslmode=require
Ejecución del proyecto
uvicorn app:app --host 0.0.0.0 --port 8002

Accede en:

http://localhost:8002
Exponer con ngrok

Para recibir webhooks desde Azure DevOps:

ngrok http 8002

Esto generará una URL pública como:

https://xxxx.ngrok-free.app

Ejemplo endpoint:

https://xxxx.ngrok-free.app/webhooks/azure-devops
🔗 Configuración en Azure DevOps
Ir a:
Project Settings → Service Hooks
Crear un nuevo webhook
Configurar:
Evento: Pull Request Created (u otros)
URL: endpoint de ngrok
Método: POST

# Endpoints principales
Webhook Azure DevOps
POST /webhooks/azure-devops

Recibe eventos como:

git.pullrequest.created
git.pullrequest.updated

# Base de datos (Neon PostgreSQL)

Se almacenan:

Eventos raw (webhook_raw)
Métricas procesadas (pr_metrics)

Ejemplo de datos guardados:

ID del PR
Estado
Autor
Fecha de creación
Tipo de evento

Dashboard Metricas de PR con azure 
<img width="1758" height="1005" alt="image" src="https://github.com/user-attachments/assets/b860fb5b-9599-4ef2-a616-21e074fe6e14" />

Conexion a la base de datos
http://127.0.0.1:8002/dashboard


<img width="1889" height="592" alt="image" src="https://github.com/user-attachments/assets/7af60e58-84ee-40f0-936a-de9715a72efa" />



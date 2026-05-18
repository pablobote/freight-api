# HappyRobot — Inbound Carrier Sales Automation

A REST API and real-time dashboard built for the HappyRobot technical challenge. The system automates inbound carrier load sales for a freight brokerage — handling carrier verification, load matching, and price negotiation via an AI voice agent.

---

## What it does

- Serves load board data to the HappyRobot voice agent via a REST API
- Receives webhook data from HappyRobot after each call (negotiation outcome, sentiment analysis, call classification)
- Tracks every carrier that calls, including their FMCSA verification data and full call history
- Displays all of this in a live analytics dashboard with real-time updates

## Stack

- **API** — FastAPI (Python 3.12)
- **Database** — Google Firestore
- **Dashboard** — Vanilla HTML/JS, served by the API
- **Container** — Docker
- **Cloud** — Google Cloud Run

---

## Access the live deployment

Dashboard:
```
https://freight-api-860948435425.us-central1.run.app/?key=123456789
```

API base URL:
```
https://freight-api-860948435425.us-central1.run.app
```

All API endpoints require the header:
```
X-API-Key: 123456789
```

---

## Run locally

```bash
git clone https://github.com/pablobote/freight-api.git
cd freight-api

# With Docker (recommended)
API_KEY=mykey docker compose up --build
# Open http://localhost:8080/?key=mykey

# Without Docker
pip install -r requirements.txt
API_KEY=mykey DATA_PATH=data/freight_data.csv uvicorn app.main:app --port 8080 --reload
```

---

## Redeploy to Google Cloud Run

```bash
docker build -t gcr.io/inbound-carrier-calls/freight-api .
docker push gcr.io/inbound-carrier-calls/freight-api

gcloud run deploy freight-api \
  --image gcr.io/inbound-carrier-calls/freight-api \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars "API_KEY=YOUR_KEY,USE_FIRESTORE=true,FIRESTORE_DB=inbound-calls-database" \
  --port 8080
```

---

## Webhook endpoints

| Endpoint | Description |
|----------|-------------|
| `POST /webhooks/offer` | Negotiation outcome — rates, route, carrier info |
| `POST /webhooks/analysis` | Sentiment, outcome, call duration, attitude |
| `POST /webhooks/negotiation` | Real-time AI decision per negotiation round |
| `POST /carriers/{mc}/fmcsa` | FMCSA verification data |

---

## Note on running the deployment

The live dashboard is publicly accessible via the URL and API key above — no credentials required.

Reproducing the full deployment from scratch requires a Google Cloud project with Cloud Run and Firestore enabled. The container image is hosted on a private GCR registry tied to the project. If you'd like to reproduce the environment independently, reach out and I'll provide access or walk you through it.

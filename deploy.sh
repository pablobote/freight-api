#!/bin/bash
# deploy.sh — Build and deploy Freight API to Google Cloud Run
# 
# Usage: ./deploy.sh YOUR_GCP_PROJECT_ID
#
# Prerequisites:
#   - gcloud CLI installed and authenticated: gcloud auth login
#   - Docker installed
#   - Google Cloud project with billing enabled
#
# Example:
#   ./deploy.sh my-gcp-project

set -e

# Validate arguments
if [ -z "$1" ]; then
  echo "❌ Error: GCP Project ID required"
  echo ""
  echo "Usage: ./deploy.sh YOUR_GCP_PROJECT_ID"
  echo ""
  echo "Example:"
  echo "  ./deploy.sh my-gcp-project"
  exit 1
fi

PROJECT_ID="$1"
SERVICE_NAME="freight-api"
REGION="us-central1"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
FIRESTORE_DB="inbound-calls-database"

# Generate API key if not provided
if [ -z "$API_KEY" ]; then
  API_KEY=$(openssl rand -hex 16)
fi

echo ""
echo "╔════════════════════════════════════════════╗"
echo "║   Freight API — Google Cloud Run Deploy   ║"
echo "╚════════════════════════════════════════════╝"
echo ""
echo "Project ID:      $PROJECT_ID"
echo "Service:         $SERVICE_NAME"
echo "Region:          $REGION"
echo "Image:           $IMAGE"
echo "API Key:         $API_KEY"
echo "Firestore DB:    $FIRESTORE_DB"
echo ""

# 1. Validate gcloud is configured
echo "📋 Checking gcloud configuration..."
if ! gcloud config get-value project > /dev/null 2>&1; then
  echo "⚠️  gcloud not configured. Run: gcloud auth login"
  exit 1
fi

# 2. Set project
echo "🔧 Setting GCP project..."
gcloud config set project "$PROJECT_ID"

# 3. Enable required APIs
echo "🔗 Enabling required APIs..."
gcloud services enable \
  run.googleapis.com \
  containerregistry.googleapis.com \
  firestore.googleapis.com \
  --project "$PROJECT_ID"

# 4. Build Docker image
echo "🔨 Building Docker image..."
docker build -t "$IMAGE" .

# 5. Configure Docker authentication
echo "🔑 Configuring Docker authentication..."
gcloud auth configure-docker --quiet

# 6. Push to Google Container Registry
echo "📤 Pushing image to GCR..."
docker push "$IMAGE"

# 7. Deploy to Cloud Run
echo "🚀 Deploying to Cloud Run..."
gcloud run deploy "$SERVICE_NAME" \
  --image "$IMAGE" \
  --platform managed \
  --region "$REGION" \
  --allow-unauthenticated \
  --set-env-vars "API_KEY=${API_KEY},USE_FIRESTORE=true,FIRESTORE_DB=${FIRESTORE_DB}" \
  --port 8080 \
  --memory 512Mi \
  --cpu 1 \
  --project "$PROJECT_ID"

# 8. Get service URL
SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
  --region "$REGION" \
  --project "$PROJECT_ID" \
  --format "value(status.url)")

echo ""
echo "╔════════════════════════════════════════════╗"
echo "║         ✅ Deployment Successful!         ║"
echo "╚════════════════════════════════════════════╝"
echo ""
echo "🌐 Service URL:"
echo "   $SERVICE_URL"
echo ""
echo "📊 Dashboard:"
echo "   $SERVICE_URL/?key=$API_KEY"
echo ""
echo "📝 API Key (save this!):"
echo "   $API_KEY"
echo ""
echo "🧪 Test health endpoint:"
echo "   curl $SERVICE_URL/health"
echo ""
echo "📞 Test with API key:"
echo "   curl -H 'X-API-Key: $API_KEY' $SERVICE_URL/carriers"
echo ""
echo "📋 View logs:"
echo "   gcloud run services logs read $SERVICE_NAME --region=$REGION --limit=100"
echo ""

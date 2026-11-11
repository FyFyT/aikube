#!/bin/bash
set -e
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'
PROJECT_DIR="$HOME/aikube"

echo -e "${BLUE}🚀 Création de la structure AI-Kube...${NC}"

mkdir -p $PROJECT_DIR/{backend,frontend,models,data,scripts,tests,docs,k8s}
mkdir -p $PROJECT_DIR/backend/{api,core,ai_module,k8s_client,security,devops,monitoring}
mkdir -p $PROJECT_DIR/backend/ai_module/{fine_tune,inference,detection,datasets}
mkdir -p $PROJECT_DIR/backend/security/{rbac,network,audit,cve}
mkdir -p $PROJECT_DIR/backend/devops/{cicd,helm,logs}
mkdir -p $PROJECT_DIR/frontend/src/{components,pages,hooks,utils,styles}
mkdir -p $PROJECT_DIR/frontend/src/components/{layout,dashboard,yaml,detection,security,devops,assistant}
mkdir -p $PROJECT_DIR/data/{raw,processed,datasets,exports}
mkdir -p $PROJECT_DIR/models/{base,fine_tuned,checkpoints}
mkdir -p $PROJECT_DIR/k8s/{deployments,services,configmaps,rbac,monitoring}
mkdir -p $PROJECT_DIR/tests/{unit,integration,e2e}

touch $PROJECT_DIR/backend/__init__.py
touch $PROJECT_DIR/backend/api/__init__.py
touch $PROJECT_DIR/backend/core/__init__.py
touch $PROJECT_DIR/backend/ai_module/__init__.py
touch $PROJECT_DIR/backend/security/__init__.py
touch $PROJECT_DIR/backend/devops/__init__.py
touch $PROJECT_DIR/backend/monitoring/__init__.py

echo -e "${GREEN}✅ Structure créée !${NC}"
echo -e "${YELLOW}📁 Dossiers:${NC}"
find $PROJECT_DIR -type d | head -40

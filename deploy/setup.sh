#!/bin/bash

# ScamHound Production Deployment Script
# For VPS with existing Nginx + PM2 setup
# Ubuntu 22.04/24.04 - Server IP: 76.13.101.39

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  ScamHound Production Deployment${NC}"
echo -e "${GREEN}  (Existing Nginx + PM2 Environment)${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# ============================================
# Step 1: Create scamhound user
# ============================================
echo -e "${YELLOW}[1/9] Creating scamhound user...${NC}"
if id "scamhound" &>/dev/null; then
    echo "User 'scamhound' already exists, skipping..."
else
    useradd -r -s /bin/false -m -d /opt/scamhound scamhound
    echo -e "${GREEN}User 'scamhound' created successfully${NC}"
fi

# ============================================
# Step 2: Create /opt/scamhound directory structure
# ============================================
echo -e "${YELLOW}[2/9] Setting up directory structure...${NC}"
mkdir -p /opt/scamhound
mkdir -p /opt/scamhound/logs
chown scamhound:scamhound /opt/scamhound
chown scamhound:scamhound /opt/scamhound/logs

# ============================================
# Step 3: Clone the repository
# ============================================
echo -e "${YELLOW}[3/9] Cloning ScamHound repository...${NC}"
if [ -d "/opt/scamhound/scamhound" ]; then
    echo "Repository already exists, pulling latest changes..."
    cd /opt/scamhound/scamhound
    sudo -u scamhound git pull origin main
else
    sudo -u scamhound git clone https://github.com/RunTimeAdmin/ScamHound.git /opt/scamhound/scamhound
fi

# ============================================
# Step 4: Create Python virtual environment
# ============================================
echo -e "${YELLOW}[4/9] Creating Python virtual environment...${NC}"
cd /opt/scamhound
if [ ! -d "venv" ]; then
    sudo -u scamhound python3 -m venv venv
fi

# ============================================
# Step 5: Install Python requirements
# ============================================
echo -e "${YELLOW}[5/9] Installing Python dependencies...${NC}"
sudo -u scamhound /opt/scamhound/venv/bin/pip install --upgrade pip
sudo -u scamhound /opt/scamhound/venv/bin/pip install -r /opt/scamhound/scamhound/requirements.txt

# ============================================
# Step 6: Set up environment file
# ============================================
echo -e "${YELLOW}[6/9] Setting up environment configuration...${NC}"
if [ -f "/opt/scamhound/.env" ]; then
    echo -e "${GREEN}.env file already exists, preserving existing configuration${NC}"
else
    if [ -f "/opt/scamhound/scamhound/.env.example" ]; then
        cp /opt/scamhound/scamhound/.env.example /opt/scamhound/.env
        chown scamhound:scamhound /opt/scamhound/.env
        chmod 600 /opt/scamhound/.env
        echo -e "${GREEN}.env file created from .env.example${NC}"
        echo ""
        echo -e "${RED}IMPORTANT: You must edit /opt/scamhound/.env and add your API keys!${NC}"
        echo "Required API keys:"
        echo "  - BAGS_API_KEY"
        echo "  - HELIUS_API_KEY"
        echo "  - BIRDEYE_API_KEY"
        echo "  - BUBBLEMAPS_API_KEY"
        echo "  - ANTHROPIC_API_KEY"
        echo ""
        echo "Edit the file with: sudo nano /opt/scamhound/.env"
        echo ""
        read -p "Press Enter to continue after you've noted this..."
    else
        echo -e "${RED}Warning: .env.example not found${NC}"
    fi
fi

# ============================================
# Step 7: Configure Nginx
# ============================================
echo -e "${YELLOW}[7/9] Configuring Nginx...${NC}"
if [ -f "/opt/scamhound/scamhound/deploy/nginx.conf" ]; then
    cp /opt/scamhound/scamhound/deploy/nginx.conf /etc/nginx/sites-available/app.scamhoundcrypto.com
    
    # Enable scamhound site (don't remove other sites)
    if [ ! -L "/etc/nginx/sites-enabled/app.scamhoundcrypto.com" ]; then
        ln -s /etc/nginx/sites-available/app.scamhoundcrypto.com /etc/nginx/sites-enabled/app.scamhoundcrypto.com
    fi
    
    # Test Nginx configuration
    nginx -t
    
    # Reload Nginx (don't restart - other sites are running)
    systemctl reload nginx
    echo -e "${GREEN}Nginx configured successfully${NC}"
else
    echo -e "${RED}Warning: nginx.conf not found in repository${NC}"
fi

# ============================================
# Step 8: Start with PM2
# ============================================
echo -e "${YELLOW}[8/9] Starting ScamHound with PM2...${NC}"
cd /opt/scamhound/scamhound

# Copy ecosystem config if not exists
if [ ! -f "/opt/scamhound/scamhound/ecosystem.config.js" ]; then
    cp /opt/scamhound/scamhound/deploy/ecosystem.config.js /opt/scamhound/scamhound/ecosystem.config.js
fi

# Stop existing process if running
sudo -u scamhound pm2 stop scamhound 2>/dev/null || true

# Start with PM2
sudo -u scamhound pm2 start ecosystem.config.js

# Save PM2 process list
sudo -u scamhound pm2 save

echo -e "${GREEN}ScamHound started with PM2${NC}"

# ============================================
# Step 9: SSL Certificate
# ============================================
echo -e "${YELLOW}[9/9] SSL Certificate Setup${NC}"
echo "Make sure your DNS A record for app.scamhoundcrypto.com points to 76.13.101.39"
echo ""

if [ -d "/etc/letsencrypt/live/app.scamhoundcrypto.com" ]; then
    echo -e "${GREEN}SSL certificate already exists for app.scamhoundcrypto.com${NC}"
else
    read -p "Do you want to obtain SSL certificate now? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        certbot --nginx -d app.scamhoundcrypto.com --non-interactive --agree-tos --email admin@scamhoundcrypto.com
        echo -e "${GREEN}SSL certificate obtained successfully${NC}"
    else
        echo -e "${YELLOW}Skipping SSL setup. Run 'certbot --nginx -d app.scamhoundcrypto.com' later.${NC}"
    fi
fi

# ============================================
# Deployment complete
# ============================================
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Deployment Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "ScamHound is deployed at: https://app.scamhoundcrypto.com"
echo ""
echo "PM2 Commands:"
echo "  Check status:     pm2 status"
echo "  View logs:        pm2 logs scamhound"
echo "  Restart:          pm2 restart scamhound"
echo "  Stop:             pm2 stop scamhound"
echo "  Delete:           pm2 delete scamhound"
echo ""
echo "Nginx:"
echo "  Test config:      sudo nginx -t"
echo "  Reload:           sudo systemctl reload nginx"
echo ""
echo "To update from GitHub:"
echo "  cd /opt/scamhound/scamhound && sudo -u scamhound git pull origin main"
echo "  pm2 restart scamhound"
echo ""

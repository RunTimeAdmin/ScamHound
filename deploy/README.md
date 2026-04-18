# ScamHound Production Deployment Guide

This guide covers deploying ScamHound to a VPS at `app.scamhoundcrypto.com`.

## Prerequisites

- **OS**: Ubuntu 22.04 LTS or 24.04 LTS
- **Python**: 3.10 or higher
- **Domain**: `app.scamhoundcrypto.com` with DNS A record pointing to your VPS IP (76.13.101.39)
- **GitHub Access**: Repository at `https://github.com/RunTimeAdmin/ScamHound.git`
- **Existing Infrastructure**: Nginx and PM2 already installed and running other sites

## DNS Setup

Before deploying, configure your DNS:

1. Log in to your domain registrar or DNS provider
2. Create an **A record** for `app.scamhoundcrypto.com` pointing to `76.13.101.39`
3. Wait for DNS propagation (can take up to 24 hours, usually 5-30 minutes)

Verify DNS with:
```bash
nslookup app.scamhoundcrypto.com
```

## Quick Deploy

Run the automated setup script as root:

```bash
curl -fsSL https://raw.githubusercontent.com/RunTimeAdmin/ScamHound/main/deploy/setup.sh | sudo bash
```

Or manually:

```bash
# Download the setup script
wget https://raw.githubusercontent.com/RunTimeAdmin/ScamHound/main/deploy/setup.sh

# Make it executable and run
chmod +x setup.sh
sudo ./setup.sh
```

## Manual Deployment Steps

If you prefer manual setup:

### 1. Create User and Directories

```bash
sudo useradd -r -s /bin/false -m -d /opt/scamhound scamhound
sudo mkdir -p /opt/scamhound/logs
sudo chown scamhound:scamhound /opt/scamhound
sudo chown scamhound:scamhound /opt/scamhound/logs
```

### 2. Clone Repository

```bash
sudo -u scamhound git clone https://github.com/RunTimeAdmin/ScamHound.git /opt/scamhound/scamhound
```

### 3. Set Up Virtual Environment

```bash
cd /opt/scamhound
sudo -u scamhound python3 -m venv venv
sudo -u scamhound venv/bin/pip install -r scamhound/requirements.txt
```

### 4. Configure Environment

```bash
sudo cp /opt/scamhound/scamhound/.env.example /opt/scamhound/.env
sudo chown scamhound:scamhound /opt/scamhound/.env
sudo chmod 600 /opt/scamhound/.env
sudo nano /opt/scamhound/.env  # Add your API keys
```

### 5. Configure Nginx

```bash
sudo cp /opt/scamhound/scamhound/deploy/nginx.conf /etc/nginx/sites-available/app.scamhoundcrypto.com
sudo ln -s /etc/nginx/sites-available/app.scamhoundcrypto.com /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### 6. Start with PM2

```bash
cd /opt/scamhound/scamhound
sudo -u scamhound pm2 start ecosystem.config.js
sudo -u scamhound pm2 save
```

### 7. Obtain SSL Certificate

```bash
sudo certbot --nginx -d app.scamhoundcrypto.com
```

## PM2 Process Management

PM2 is used to manage the ScamHound process. The following commands are available:

### Check Status

```bash
pm2 status
```

### View Logs

```bash
# View logs in real-time
pm2 logs scamhound

# View last 100 lines
pm2 logs scamhound --lines 100

# View error logs only
pm2 logs scamhound --err
```

### Restart Service

```bash
pm2 restart scamhound
```

### Stop Service

```bash
pm2 stop scamhound
```

### Remove from PM2

```bash
pm2 delete scamhound
```

### Save PM2 Process List

```bash
pm2 save
```

## Updating from GitHub

To update ScamHound to the latest version:

```bash
# Pull latest changes
sudo -u scamhound bash -c "cd /opt/scamhound/scamhound && git pull origin main"

# Restart with PM2
sudo -u scamhound pm2 restart scamhound

# Check status
pm2 status
```

## Nginx Management

### Test Configuration

```bash
sudo nginx -t
```

### Reload Nginx

```bash
sudo systemctl reload nginx
```

### View Nginx Logs

```bash
# Error logs
sudo tail -f /var/log/nginx/error.log

# Access logs
sudo tail -f /var/log/nginx/access.log
```

## SSL Certificate Management

### Check Certificate Status

```bash
sudo certbot certificates
```

### Renew Manually

```bash
sudo certbot renew
```

### Test Auto-Renewal

```bash
sudo certbot renew --dry-run
```

Certbot automatically installs a systemd timer for certificate renewal. Certificates will auto-renew 30 days before expiration.

## Troubleshooting

### Service Won't Start

```bash
# Check PM2 logs
pm2 logs scamhound --lines 50

# Check PM2 error logs
cat /opt/scamhound/logs/scamhound-error.log

# Verify Python environment
sudo -u scamhound /opt/scamhound/venv/bin/python --version

# Test configuration
sudo -u scamhound bash -c "cd /opt/scamhound/scamhound && source /opt/scamhound/venv/bin/activate && python -c 'from dashboard.app import app; print(\"OK\")'"
```

### Nginx Errors

```bash
# Test configuration
sudo nginx -t

# Check Nginx error logs
sudo tail -f /var/log/nginx/error.log

# Check Nginx access logs
sudo tail -f /var/log/nginx/access.log
```

### SSL Certificate Issues

```bash
# Check certificate status
sudo certbot certificates

# Renew manually
sudo certbot renew

# Reinstall certificate
sudo certbot --nginx -d app.scamhoundcrypto.com
```

### Database Permissions

```bash
# Ensure scamhound user owns the database directory
sudo chown -R scamhound:scamhound /opt/scamhound/scamhound
sudo chmod 755 /opt/scamhound/scamhound
```

## Security Notes

- The `.env` file contains sensitive API keys and is only readable by the `scamhound` user
- Rate limiting is configured for API endpoints (5 scans/minute per IP)
- Nginx adds security headers to all responses
- SSL certificates are automatically renewed
- The application runs behind Nginx reverse proxy on port 8000

## Support

For issues or questions:
- GitHub Issues: https://github.com/RunTimeAdmin/ScamHound/issues
- Twitter: @ScamHoundCrypto

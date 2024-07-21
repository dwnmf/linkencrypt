#!/bin/bash

# Automated Flask Deployment Script (Without Virtual Environment)

# Check if the script is run with sudo
if [ "$EUID" -ne 0 ]
  then echo "Please run as root or with sudo"
  exit
fi

# Prompt for the app directory
read -p "Enter the full path to your Flask app directory: " APP_DIR

# Validate the directory
if [ ! -d "$APP_DIR" ]; then
    echo "Error: Directory does not exist."
    exit 1
fi

# Install required packages
apt-get update
apt-get install -y python3-pip nginx supervisor

# Install Flask and Gunicorn globally
pip3 install Flask gunicorn

# Create a WSGI entry point
cat > "$APP_DIR/wsgi.py" << EOL
from app import app

if __name__ == "__main__":
    app.run()
EOL

# Configure Nginx
cat > /etc/nginx/sites-available/flask_app << EOL
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://unix:/tmp/flask_app.sock;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
}
EOL

ln -s /etc/nginx/sites-available/flask_app /etc/nginx/sites-enabled/
rm /etc/nginx/sites-enabled/default

# Configure Supervisor
cat > /etc/supervisor/conf.d/flask_app.conf << EOL
[program:flask_app]
directory=$APP_DIR
command=gunicorn wsgi:app -b unix:/tmp/flask_app.sock
user=www-data
autostart=true
autorestart=true
stopasgroup=true
killasgroup=true
EOL

# Set up environment variables
cat > "$APP_DIR/.env" << EOL
FLASK_APP=app.py
FLASK_ENV=production
EOL

# Ensure proper permissions
chown -R www-data:www-data "$APP_DIR"
chmod -R 755 "$APP_DIR"

# Restart services
systemctl restart nginx
systemctl restart supervisor

echo "Deployment complete. Your Flask app should now be running."
echo "Please ensure your firewall allows traffic on port 80."
echo "You may need to configure your domain DNS to point to this server's IP."
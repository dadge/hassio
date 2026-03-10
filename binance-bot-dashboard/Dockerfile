# Binance Grid Bot Dashboard - Home Assistant Add-on
FROM node:20-alpine

# Install required packages
RUN apk add --no-cache curl jq bash nginx supervisor

# Copy nginx config
COPY nginx.conf /etc/nginx/nginx.conf

# Copy Angular build
COPY www /usr/share/nginx/html

# Create assets directory for config
RUN mkdir -p /usr/share/nginx/html/assets

# Copy backend
COPY backend /backend
WORKDIR /backend
RUN npm ci --only=production

# Create data directory
RUN mkdir -p /data && chown -R node:node /data

# Create supervisor config
RUN mkdir -p /etc/supervisor.d
COPY supervisord.ini /etc/supervisor.d/supervisord.ini

# Copy run script
COPY run.sh /run.sh
RUN chmod +x /run.sh

EXPOSE 4201

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:4201/ && curl -f http://localhost:3000/api/health || exit 1

CMD ["/run.sh"]

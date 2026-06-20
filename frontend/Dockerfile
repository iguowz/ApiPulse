# Build stage
FROM node:20-alpine AS build

WORKDIR /app
COPY frontend/package.json frontend/vite.config.js ./
RUN npm install

COPY frontend/index.html ./
COPY frontend/src/ ./src/
RUN npm run build

# Serve stage
FROM nginx:alpine

COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

# 确保 nginx 日志目录存在（bind mount 时防止权限问题）
RUN mkdir -p /var/log/nginx

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]

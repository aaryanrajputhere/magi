# syntax=docker/dockerfile:1
FROM node:22-slim
WORKDIR /app/frontend
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --prefer-offline
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev"]

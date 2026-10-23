FROM node:26-alpine AS build
WORKDIR /app
RUN npm install -g pnpm@12.10.1
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY index.html ./
COPY tsconfig.app.json ./
COPY vite.config.ts ./
COPY src ./src
COPY public ./public
RUN pnpm build

FROM caddy:2.11-alpine
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /srv
EXPOSE 80 6006

FROM node:20-bookworm-slim

WORKDIR /app

RUN corepack enable
RUN corepack prepare pnpm@9.0.0 --activate

COPY package.json /app/package.json
RUN pnpm install

COPY . /app

EXPOSE 3000

CMD ["pnpm", "dev", "--hostname", "0.0.0.0", "--port", "3000"]

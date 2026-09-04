# 使用轻量级的 Node Alpine 镜像
FROM node:20-alpine

# 设置工作目录
WORKDIR /app

# 将项目的所有文件拷贝到容器的工作目录中
COPY . .

# 暴露 start-server.js 中定义的 8080 端口
EXPOSE 8080

# 启动静态资源服务器
CMD ["node", "start-server.js"]

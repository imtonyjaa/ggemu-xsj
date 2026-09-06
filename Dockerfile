# 使用轻量级的 Node Alpine 镜像
FROM node:20-alpine

# 设置工作目录
WORKDIR /app

# 将项目的所有文件拷贝到容器的工作目录中
COPY . .

# 从云端数据镜像中提取 1.8GB 静态资源 (请确保构建前，你已推送了该基础镜像)
# 这里假设你的 Docker Hub 用户名是 itdashuai (如果是其他的请自行修改)
COPY --from=itdashuai/miu2d-assets:v1 /data /app/miu2d.williamchan.me

# 暴露 start-server.js 中定义的 8080 端口
EXPOSE 8080

# 启动静态资源服务器
CMD ["node", "start-server.js"]

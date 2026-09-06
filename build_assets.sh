#!/bin/bash
set -e

# 这里填入你的 Docker Hub 用户名
DOCKER_USERNAME="itdashuai"
IMAGE_NAME="miu2d-assets:v1"

echo "开始构建数据镜像 ${DOCKER_USERNAME}/${IMAGE_NAME} ..."
docker build -f Dockerfile.assets -t ${DOCKER_USERNAME}/${IMAGE_NAME} .

echo "开始推送镜像到 Docker Hub ..."
docker push ${DOCKER_USERNAME}/${IMAGE_NAME}

echo "完成！现在你可以提交 git 更改并在 Coolify 上触发部署了。"

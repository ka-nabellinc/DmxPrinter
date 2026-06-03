#!/bin/zsh

RELEASE_VERSION=$1
CURRENT_DIR=$(cd $(dirname $0);pwd)
TAG="harbor.kmrn.dev/sleevePrinterInterfaceDriver:${RELEASE_VERSION}"

if [$RELEASE_VERSION = ""]; then
	echo "Enter the release version"
	exit 1
fi

cd ${CURRENT_DIR}
docker buildx build . -t $TAG --platform=linux/arm64,linux/amd64,linux/arm/v7 --push

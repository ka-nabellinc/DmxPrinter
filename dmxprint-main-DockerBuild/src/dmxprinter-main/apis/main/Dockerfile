FROM ubuntu:jammy-20250126

ENV TZ=Asia/Tokyo
ENV PYTHONUNBUFFERED=1

RUN apt update && apt install gnupg -y
RUN apt-key adv --recv-keys --keyserver keyserver.ubuntu.com 0xcbcb082a1bb943db
RUN apt update && apt upgrade -y && apt dist-upgrade -y
RUN apt install -y tzdata

RUN apt-get update \
    && apt-get install -y ca-certificates git nano python3 python3-pip
RUN pip3 install gpiozero lgpio RPI.GPIO 

RUN mkdir /home/Python
COPY src /home/Python

WORKDIR /home

RUN chmod -R 766 /home/Python
WORKDIR /home/Python/src
RUN rm -rf .git
CMD ["python3", "/home/Python/src/main/main.py"]

FROM ubuntu:jammy-20250730

ENV TZ=Asia/Tokyo

RUN apt update && apt install gnupg -y
RUN apt-key adv --recv-keys --keyserver keyserver.ubuntu.com 0xcbcb082a1bb943db
RUN apt update && apt upgrade -y && apt dist-upgrade -y
RUN apt install -y tzdata

RUN apt install -y git nano python3 python3-pip
RUN pip3 install pyserial requests

RUN mkdir /home/Python
COPY src /home/Python

WORKDIR /home

RUN chmod -R 766 /home/Python/sleevePrinterInterfaceDriver

WORKDIR /

CMD ["python3", "/home/Python/sleevePrinterInterfaceDriver/main.py"]

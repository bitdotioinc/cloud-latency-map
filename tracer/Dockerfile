FROM python:3.11.1-bullseye

RUN apt install curl ca-certificates gnupg
RUN curl https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor | tee /etc/apt/trusted.gpg.d/apt.postgresql.org.gpg >/dev/null
RUN echo "deb http://apt.postgresql.org/pub/repos/apt bullseye-pgdg main" > /etc/apt/sources.list.d/pgdg.list
RUN apt-get update -y
RUN apt-get install -y mtr postgresql-14


WORKDIR /usr/src/app

COPY requirements.txt ./
# traceroute generates raw packets, so we need to be root
USER root

# mtr-packet, which we use, is in /usr/local/sbin
ENV PATH="/usr/local/sbin:${PATH}"

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD [ "python", "./tracer.py" ]

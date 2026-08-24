#!/bin/sh
set -eu

jwt_secret="$(openssl rand -hex 32)"
source_password="$(openssl rand -hex 20)"

sed -i "s/REPLACE_JWT_SECRET/${jwt_secret}/; s/REPLACE_ICECAST_SOURCE_PASSWORD/${source_password}/" \
  /etc/openstudio/openstudio.env
sed -i "0,/<source-password>[^<]*<\/source-password>/s//<source-password>${source_password}<\/source-password>/" \
  /etc/icecast2/icecast.xml
chmod 0600 /etc/openstudio/openstudio.env

#!/usr/bin/env bash

aws ssm start-session \
  --target i-0389b349e3d4ce491 \
  --document-name AWS-StartPortForwardingSessionToRemoteHost \
  --parameters '{"host":["app-prod-db.c1uyi2mcmlxe.ap-northeast-1.rds.amazonaws.com"],"portNumber":["5432"],"localPortNumber":["5432"]}' \
  --region ap-northeast-1
FROM alpine:3.20
RUN apk add --no-cache gnupg
COPY encrypt-backups.sh /encrypt-backups.sh
COPY decrypt-backup.sh /decrypt-backup.sh
RUN chmod +x /encrypt-backups.sh /decrypt-backup.sh
ENTRYPOINT ["/encrypt-backups.sh"]

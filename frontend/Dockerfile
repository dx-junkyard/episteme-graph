FROM nginx:1.27-alpine

# Remove default config
RUN rm /etc/nginx/conf.d/default.conf

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY public /usr/share/nginx/html

# nginx のワーカーは非特権ユーザー(nginx)で動くため、ホスト側のディレクトリ権限が
# 750 などだと COPY 後も「others 不可」のまま焼き込まれ、stat() が Permission denied
# になり try_files が index.html にフォールバックして CSS/JS が壊れる。
# 配信物は全ユーザーが読めるよう正規化する（ファイルは読み取り、ディレクトリは走査可）。
RUN chmod -R a+rX /usr/share/nginx/html

EXPOSE 3000

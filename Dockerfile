FROM networktocode/nautobot:2.3-py3.11
WORKDIR /app
COPY server.py .
USER root
ENTRYPOINT ["python"]
CMD ["server.py"]

FROM sapi-native-checkout-recovery-verifier:phase1
COPY . /tests
RUN find /tests /opt/sapi-submission.py /opt/source-manifest.json -type f -exec sha256sum {} + | sort > /opt/native-sources.sha256

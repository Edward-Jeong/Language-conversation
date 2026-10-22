"""Generate private LAN CA/server certs without installing trust or changing OS settings."""
import argparse,datetime,ipaddress,pathlib
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
p=argparse.ArgumentParser();p.add_argument('--host',required=True);a=p.parse_args()
out=pathlib.Path('.cert');out.mkdir(exist_ok=True)
if any(out.iterdir()):raise SystemExit('.cert is not empty. Refusing to replace existing keys.')
now=datetime.datetime.now(datetime.timezone.utc)
key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Language Conversation Personal LAN CA')])
ca=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=5)).not_valid_after(now+datetime.timedelta(days=365)).add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True).add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),critical=False).add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),critical=False).add_extension(x509.KeyUsage(digital_signature=True,content_commitment=False,key_encipherment=False,data_encipherment=False,key_agreement=False,key_cert_sign=True,crl_sign=True,encipher_only=None,decipher_only=None),critical=True).sign(key,hashes.SHA256())
server=rsa.generate_private_key(public_exponent=65537,key_size=2048)
try:host=x509.IPAddress(ipaddress.ip_address(a.host))
except ValueError:host=x509.DNSName(a.host)
cert=x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,a.host)])).issuer_name(name).public_key(server.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=5)).not_valid_after(now+datetime.timedelta(days=90)).add_extension(x509.SubjectAlternativeName([host,x509.DNSName('localhost'),x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False).add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True).add_extension(x509.SubjectKeyIdentifier.from_public_key(server.public_key()),critical=False).add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),critical=False).add_extension(x509.KeyUsage(digital_signature=True,content_commitment=False,key_encipherment=True,data_encipherment=False,key_agreement=False,key_cert_sign=False,crl_sign=False,encipher_only=None,decipher_only=None),critical=True).add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),critical=False).sign(key,hashes.SHA256())
for filename,obj in [('ca.pem',ca),('server.pem',cert)]: (out/filename).write_bytes(obj.public_bytes(serialization.Encoding.PEM))
for filename,obj in [('ca-key.pem',key),('server-key.pem',server)]: (out/filename).write_bytes(obj.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
(out/'ca.cer').write_bytes(ca.public_bytes(serialization.Encoding.DER))
print('Created .cert. Trust is NOT installed. Transfer only ca.cer to your own iPhone. Keep all private keys private.')

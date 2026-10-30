"""Device-centric profile adaptation without mixing shell and file ports."""
SSH_FAMILY={'SSH','SFTP','SCP'}
FILE_PROTOCOLS=('SFTP','SCP','FTP','FTPS','TFTP')
DEFAULT_PORTS={'SSH':22,'SFTP':22,'SCP':22,'FTP':21,'FTPS':21,'TFTP':69,'Telnet':23,'Mitel TLS Telnet':2000}


def compatible(profile,protocols):
    source=profile.get('protocol','SSH')
    if source in protocols:return True
    if source in SSH_FAMILY and (SSH_FAMILY.intersection(protocols) or set(FILE_PROTOCOLS).intersection(protocols)):return True
    return 'SSH' in protocols and bool(profile.get('ssh_enabled'))


def for_context(profile,protocols,current=None):
    """Return a contextual copy; the vault retains one canonical device record."""
    result=dict(profile);source=profile.get('protocol','SSH')
    files=bool(set(protocols).intersection(FILE_PROTOCOLS)) and 'SSH' not in protocols
    if files and source in SSH_FAMILY:
        protocol=profile.get('file_protocol',source if source in ('SFTP','SCP') else 'SFTP')
        if protocol not in protocols:protocol='SFTP' if 'SFTP' in protocols else protocols[0]
        result['protocol']=protocol
        configured=profile.get('file_protocol',source if source!='SSH' else 'SFTP')
        result['port']=int(profile.get('file_port',profile.get('port',22)) if protocol in SSH_FAMILY and configured in SSH_FAMILY else profile.get('file_port',DEFAULT_PORTS[protocol]) if protocol==configured else DEFAULT_PORTS[protocol])
        result['auto_connect']=profile.get('file_auto_connect',profile.get('auto_connect',False) if protocol in SSH_FAMILY else False)
        if protocol not in SSH_FAMILY:
            for key in ('user','password','passphrase','key','key_data','credential_ref'):result[key]=''
            result.update(profile.get('file_login',{}));result['remember']=profile.get('file_remember',False)
    elif 'SSH' in protocols and (source in SSH_FAMILY or profile.get('ssh_enabled')):
        result['protocol']='SSH';result['port']=int(profile.get('ssh_port',profile.get('port',22) if source in SSH_FAMILY else 22))
    if result['protocol'] not in protocols:raise ValueError('This device does not support the selected connection type.')
    return result


def merge_profile(previous,settings):
    protocol=settings['protocol']
    if previous.get('protocol')=='SSH' and protocol in ('FTP','FTPS','TFTP'):
        merged=dict(previous)
        for key in ('host','group'):
            if key in settings:merged[key]=settings[key]
        merged.update(file_protocol=protocol,file_port=int(settings['port']),file_auto_connect=settings.get('auto_connect',False),file_remember=settings.get('remember',False),remote_path=settings.get('remote_path','.'))
        merged['file_login']={k:settings[k] for k in ('user','password','credential_ref','ca','unverified') if k in settings} if protocol!='TFTP' else {}
        return merged
    merged=dict(previous);merged.update(settings)
    if protocol=='SSH':
        merged['ssh_port']=int(settings['port']);merged['ssh_enabled']=True
        merged.setdefault('file_protocol','SFTP');merged.setdefault('file_port',settings['port'])
    elif protocol in FILE_PROTOCOLS:
        merged['file_protocol']=protocol;merged['file_port']=int(settings['port'])
        if protocol in SSH_FAMILY:
            merged.setdefault('ssh_port',previous.get('port',settings['port']) if previous.get('protocol')=='SSH' else settings['port'])
            merged['ssh_enabled']=True
        if previous.get('protocol')=='SSH':
            merged['protocol']='SSH';merged['port']=merged['ssh_port']
    if protocol in ('Telnet','Mitel TLS Telnet','Local terminal'):
        for key in ('ssh_port','file_protocol','file_port','file_login','file_remember','file_auto_connect','open_files'):merged.pop(key,None)
        merged['ssh_enabled']=False
    return merged

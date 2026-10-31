"""Per-connection legacy choices and GUI-safe SSH authentication prompts."""
import paramiko


def add_supported(current,extra,known):return tuple(dict.fromkeys(tuple(current)+tuple(name for name in extra if name in known)))


class ConsoleTransport(paramiko.Transport):
    def __init__(self,sock,prompt=None,password=None,legacy=False,**kwargs):
        super().__init__(sock,**kwargs);self.login_prompt=prompt;self.login_password=password;self.password_used=False
        if legacy:
            options=self.get_security_options()
            options.kex=add_supported(options.kex,('diffie-hellman-group14-sha1','diffie-hellman-group-exchange-sha1','diffie-hellman-group1-sha1'),self._kex_info)
            options.ciphers=add_supported(options.ciphers,('aes128-cbc','aes192-cbc','aes256-cbc','3des-cbc'),self._cipher_info)
            options.key_types=add_supported(options.key_types,('ssh-rsa','ssh-dss'),self._key_info)
            options.digests=add_supported(options.digests,('hmac-sha1','hmac-sha1-96','hmac-md5','hmac-md5-96'),self._mac_info)
    def challenges(self,title,instructions,prompts):
        answers=[]
        for message,echo in prompts:
            if not echo and not self.password_used and self.login_password is not None and ('password' in message.casefold() or not message.strip()):
                answer=self.login_password;self.password_used=True
            elif self.login_prompt:
                detail='\n'.join(part for part in (instructions,message) if part)
                answer=self.login_prompt(title or 'SSH login',detail or 'Authentication response:',not echo)
                if answer is None:raise paramiko.AuthenticationException('SSH login cancelled.')
            else:raise paramiko.AuthenticationException('The server requires an interactive login. Enter a password or enable interactive prompts.')
            answers.append(answer)
        return answers
    def auth_password(self,username,password,event=None,fallback=True):
        try:return super().auth_password(username,password,event=event,fallback=False)
        except paramiko.BadAuthenticationType as error:
            if event is None and fallback and 'keyboard-interactive' in error.allowed_types:return self.auth_interactive(username,self.challenges)
            raise
    def auth_interactive_dumb(self,username,handler=None,submethods=''):
        # The stock method reads stdin, which a packaged desktop GUI does not have.
        return self.auth_interactive(username,self.challenges,submethods=submethods)


class ConsoleSSHClient(paramiko.SSHClient):
    def __init__(self,settings,prompt=None):
        super().__init__();self.mode=settings.get('ssh_auth','auto');self.login_prompt=prompt
    def _auth(self,username,password,pkey,key_filenames,allow_agent,look_for_keys,gss_auth,gss_kex,gss_deleg_creds,gss_host,passphrase):
        transport=self._transport
        def finish(remaining):
            if not transport.is_authenticated() and 'keyboard-interactive' in (remaining or []):transport.auth_interactive(username,transport.challenges)
            if not transport.is_authenticated():raise paramiko.AuthenticationException('SSH authentication is incomplete. Check the selected login method.')
        if self.mode=='interactive':finish(transport.auth_interactive(username,transport.challenges));return
        if self.mode=='password' or (self.mode=='auto' and password is not None and not pkey and not key_filenames):
            if password is None:
                if not self.login_prompt:raise paramiko.AuthenticationException('Enter an SSH password first.')
                password=self.login_prompt('SSH password',f'Password for {username}:',True)
                if password is None:raise paramiko.AuthenticationException('SSH login cancelled.')
            transport.login_password=password;finish(transport.auth_password(username,password));return
        if self.mode=='keys':password=None
        try:
            super()._auth(username,password,pkey,key_filenames,allow_agent,look_for_keys,gss_auth,gss_kex,gss_deleg_creds,gss_host,passphrase)
            if transport.is_authenticated():return
        except paramiko.SSHException:
            if self.mode=='keys' or password is not None or not transport.is_active():raise
        # No saved password/key: discover the allowed methods and ask in the GUI.
        try:
            remaining=transport.auth_none(username)
            if transport.is_authenticated():return
        except paramiko.BadAuthenticationType as error:remaining=error.allowed_types
        except paramiko.AuthenticationException:remaining=[]
        if 'keyboard-interactive' in remaining:finish(transport.auth_interactive(username,transport.challenges));return
        if 'password' in remaining and self.login_prompt:
            password=self.login_prompt('SSH password',f'Password for {username}:',True)
            if password is None:raise paramiko.AuthenticationException('SSH login cancelled.')
            transport.login_password=password;finish(transport.auth_password(username,password));return
        raise paramiko.AuthenticationException('No usable SSH login credentials. Enter a password, choose a private key/agent, or select Keyboard-interactive for device login prompts.')


def transport_factory(settings,prompt):
    legacy=settings.get('ssh_legacy',False)
    def create(sock,**kwargs):
        if legacy:
            disabled=dict(kwargs.get('disabled_algorithms') or {})
            disabled['pubkeys']=list(dict.fromkeys(list(disabled.get('pubkeys',[]))+['rsa-sha2-512','rsa-sha2-256']))
            kwargs['disabled_algorithms']=disabled
        return ConsoleTransport(sock,prompt=prompt,password=settings.get('password') or None,legacy=legacy,**kwargs)
    return create

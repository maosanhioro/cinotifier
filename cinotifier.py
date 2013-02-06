#!/usr/bin/env python
# encoding: utf-8
'''
cinotifier

A script that notify to Skype commit status for svn, git(gerrit).
If you use gerrit mode, you must be enabled "gerrit query" in your environment.

@author:     maosanhioro <maosanhioro@gmail.com>
@copyright:  2013- maosanhioro
@license:    BSD License
'''

from argparse import ArgumentParser
from argparse import RawDescriptionHelpFormatter
import commands
import datetime
import json
import os
import re
import signal
import sys
import time

import ConfigParser
import Skype4Py # @see: https://github.com/awahlig/skype4py
import xml.etree.ElementTree as et

__version__ = 0.1

DEBUG = 0
INTERVAL = '60'

class CLIError(Exception):
    '''Generic exception to raise and log different fatal errors.'''
    def __init__(self, msg):
        super(CLIError).__init__(type(self))
        self.msg = "E: %s" % msg
    def __str__(self):
        return self.msg
    def __unicode__(self):
        return self.msg

class Observer(object):
    
    def __init__(self):
        home = os.path.expanduser('~')
        prog = os.path.basename(sys.argv[0])[0:-3]
        
        self._path = '%s/.%s' % (home, prog)
        
        self.config = None
        self.chat = None
    
    def set_modefile(self, mode):
        self._lock = '%s/.%s.lock' % (self._path, mode)
        self._last = '%s/.%s.last' % (self._path, mode)
        self._ini = '%s/%s.ini' % (self._path, mode)
        return self
    
    def isfile(self):
        if os.path.isfile(self._ini) is False:
            raise CLIError('Not found ini file: %s' % self._ini)
        
    def init(self, args):
        self.set_modefile(args.mode)
        
        chat_name = raw_input('Skype chat name: ')
        
        config = ConfigParser.SafeConfigParser()
        config.add_section('global')
        config.set('global', 'mode', args.mode)
        config.set('global', 'interval', INTERVAL)
        config.set('global', 'chat_name', chat_name)
        
        klass = '%sLog' % args.mode.capitalize()
        slog = globals()[klass]()
        config = slog.setup(config)
        
        if os.path.isdir(self._path) is False:
            os.mkdir(self._path)
            
        with open(self._ini, 'w') as f:
            config.write(f)
            
        if args.mode == 'svn':
            slog.set_config('repos_dir', config.get('source', 'repos_dir'))
            slog.set_info()
            self.save_last_rev(slog.get_latest_rev())
        else:
            self.save_last_updated()
        
    def start(self, args):
        self.set_modefile(args.mode).isfile()
        
        with open(self._lock, 'w') as f:
            f.write(str(os.getpid()))
            
        self.config = ConfigParser.SafeConfigParser()
        self.config.read(self._ini)
        
        klass = '%sLog' % (self.config.get('global', 'mode')).capitalize()
        slog = globals()[klass]()
        slog.set_config_dict(self.config.items('source'))
        
        if not DEBUG:
            skype = Skype4Py.Skype()
            skype.Attach()
            for c in skype.Chats:
                if c.Topic == unicode(self.config.get('global', 'chat_name'), 'utf-8'):
                    self.chat = c
                    break
                
        while True:
            msg = slog.get(self.get_last_updated())
            
            if args.mode == 'svn':
                self.save_last_rev(slog.get_final_rev())
            else:
                self.save_last_updated()
            
            if msg:
                if self.chat is None:
                    print(msg)
                else:
                    self.chat.SendMessage(msg)
                
            time.sleep(self.config.getint('global', 'interval'))
    
    def stop(self, args=None):
        if args is not None:
            self.set_modefile(args.mode).isfile()
            
        with open(self._lock, 'r') as f:
            pid = int(f.read())
        
        os.remove(self._lock)
        os.kill(pid, signal.SIGKILL)
        
    def get_last_updated(self):
        with open(self._last, 'r') as f:
            return int(f.read())

    def save_last_updated(self):
        with open(self._last, 'w') as f:
            f.write(str(int(time.time())))
            
    def save_last_rev(self, value=None):
        if value is not None:
            with open(self._last, 'w') as f:
                f.write(str(value))
            
class SourceLog(object):
    
    def set_config(self, key, value):
        setattr(self, key, value)
    
    def set_config_dict(self, value):
        for k, v in value:
            setattr(self, k, v)
        
    def getoutput(self, f, t=None):
        if t:
            f = f % t
        return commands.getoutput(f)
    
    def to_msg(self, l=[]):
        result = ''
        if l:
            result = '\n\n'.join(l)
        return result
    
    def setup(self, config):
        repos_dir = raw_input('Repository directory path: ')
        config.add_section('source')
        config.set('source', 'repos_dir', repos_dir)
        return config
    
    def get(self):
        pass
    
class SvnLog(SourceLog):
    
    def __init__(self):
        self._revlist = []
        
    def set_info(self): 
        cmd = 'cd %s; svn info --xml -r HEAD 2>&1'
        data = self.getoutput(cmd, self.repos_dir)
        obj = et.fromstring(data)
        
        self._name = obj.getiterator('entry')[0].get('path')
        self._latest_rev = obj.getiterator('entry')[0].get('revision')
    
    def get_latest_rev(self):
        return self._latest_rev
        
    def get_final_rev(self):
        if len(self._revlist) > 0:
            return max(self._revlist)
    
    def get(self, last_rev):
        self.set_info()
        
        msglist = []
        if  int(last_rev) < int(self._latest_rev):
            cmd = 'cd %s; svn log --xml -v -r %s:HEAD 2>&1'
            data = self.getoutput(cmd, (self.repos_dir, last_rev + 1))
            obj = et.fromstring(data)
            for e in list(obj):
                rev = e.get('revision')
                author = e.getiterator('author')[0].text
                subject = (e.getiterator('msg')[0].text)
                subject = subject.encode('utf-8') if subject else ''
                files = ''
                for p in e.findall('.//path'):
                    files = files + '  [%s] %s\n' % (p.get('action'), (p.text).encode('utf-8'))
                f = '(*) [SvnLog] %s  Rev: %s / Author: %s\n%s\n%s'
                msg = f % (self._name, rev, author, subject, files)
                msglist.append(msg)
                self._revlist.append(rev)
        
        return self.to_msg(msglist)
    
class GitLog(SourceLog):
    
    def get(self, last_updated):
        data = self.getoutput('cd %s; git fetch 2>&1', self.repos_dir)
        
        pname = ''
        msglist = []
        for line in data.split('\n'):
            items = line.split()
            
            if re.search(r'From (.*)', line) != None:
                pname = items[-1].split('/')[-1]
                items = []
                continue
            
            if re.search(r'\w+\.\.\w(.*)', line) != None:
                # Commit log
                cmd = "cd %s; git log %s" % (self.repos_dir, items[0])
                cmd = cmd + " --pretty=format:'%h - %s (%ar) [%an]'"
                ci = self.getoutput(cmd)
                msglist.append('(*) [GitLog] Commit branch %s/%s:\n%s' % (pname, items[1], ci))
                
            elif re.search(r'\* \[new branch(.*)', line) != None:
                # Branch log
                msglist.append('\o/ [GitLog] Branch %s/%s' % (pname, items[3]))
                
            elif re.search(r'\* \[new tag(.*)', line) != None:
                # Tag log
                msglist.append('(d) [GitLog] Tag %s/%s' % (pname, items[3]))
        
        return self.to_msg(msglist)

class GerritLog(SourceLog):
        
    def setup(self, config):
        project = raw_input('Gerrit project name: ')
        host = raw_input('Gerrit host: ')
        config.add_section('source')
        config.set('source', 'project', project)
        config.set('source', 'host', host)
        return config
    
    def get(self, last_updated):
        cmd = 'ssh %s -- "gerrit query --format=JSON project:%s branch:master is:open limit:%s"'
        data = self.getoutput(cmd, (self.host, self.project, 3))
        
        msglist = []
        for line in data.split('\n'):
            obj = json.loads(line)
            if len(obj) == 3: continue
            
            if last_updated < int(obj['lastUpdated']):
                branch = '%s/%s' % (obj['project'], obj['branch'])
                url = 'url: https://%s/#change,%s' % (self.host, obj['number'])
                owner = obj['owner']['name']
                gid = 'id: %s' % obj['id']
                subject = obj['subject']
                date = datetime.datetime.fromtimestamp(obj['lastUpdated'])
                msglist.append('(*) [GerritLog] branch %s:\n%s\n%s(%s) [%s]\n%s' % (branch, gid, subject, date, owner, url))
                
        return self.to_msg(msglist)

def check_env(args=None):
    if sys.maxsize > 2**32:
        print('Python is not 32bit, probably 64bit.')
        print('''
This script does not work on Python 64bit.
If you are using Python 64bit:
    $ export VERSIONER_PYTHON_PREFER_32_BIT=yes
    
Or using virtualenv on Python 64bit:
    $ lipo -info $HOME/.virtualenvs/foo/bin/python
    $ mv $HOME/.virtualenvs/foo/bin/python $HOME/.virtualenvs/foo/bin/python.old
    $ lipo -remove x86_64 $HOME/.virtualenvs/foo/bin/python.old -output $HOME/.virtualenvs/foo/bin/python
            ''')
    else:
        print('Python is 32bit.')
        
def main(argv=None):
    '''Command line options.'''
    
    if argv is None:
        argv = sys.argv
    else:
        sys.argv.extend(argv)

    program_version_message = '%%(prog)s %s' % (__version__)
    
    try:
        parser = ArgumentParser(formatter_class=RawDescriptionHelpFormatter)
        parser.add_argument('-V', '--version', action='version', version=program_version_message)
        subparsers = parser.add_subparsers()
        
        parser_check = subparsers.add_parser('check')
        parser_check.set_defaults(func=check_env)
        
        choices = ('svn', 'git', 'gerrit')
        ob = Observer()
        
        parser_init = subparsers.add_parser('init')
        parser_init.add_argument('mode', type=str, choices=choices)
        parser_init.set_defaults(func=ob.init)
        
        parser_start = subparsers.add_parser('start')
        parser_start.add_argument('mode', type=str, choices=choices)
        parser_start.set_defaults(func=ob.start)
        
        parser_stop = subparsers.add_parser('stop')
        parser_stop.add_argument('mode', type=str, choices=choices)
        parser_stop.set_defaults(func=ob.stop)
        
        args = parser.parse_args()
        args.func(args)
        
        return 0
    except KeyboardInterrupt:
        ob.stop()
        return 0
    except Exception, e:
        if DEBUG:
            raise CLIError(e)
        return 2

if __name__ == "__main__":
    sys.exit(main())
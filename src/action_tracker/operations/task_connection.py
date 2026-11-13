"""Connection instrumentation, active ONLY for an explicitly enabled task policy.

TEMP triggers capture actual row deltas; no persistent trigger/schema changes.
Durable intent precedes COMMIT. A missing receipt is reconciled, never retried
blindly. All original business validation continues to run inside its transaction.
"""
import json
import sqlite3

from .task_backup import (manager_for, policy_enabled, quote, identity, digest, StorageError,
                          PostCommitEvidenceError)


def first_keyword(sql):
    text=sql.lstrip()
    while text.startswith(('--','/*')):
        end=text.find('\n')+1 if text.startswith('--') else text.find('*/')+2
        if end<=1:return ''
        text=text[end:].lstrip()
    return text.split(None,1)[0].upper().rstrip(';') if text else ''


def encoded(columns, alias):
    # json_object's argument limit varies by SQLite build. Chunk wide tables.
    chunks=[]
    for offset in range(0,len(columns),24):
        pairs=[]
        for column in columns[offset:offset+24]:
            value=alias+'.'+quote(column)
            pairs += ["'"+column.replace("'","''")+"'",
                      "CASE WHEN typeof("+value+")='blob' THEN json_object('$blob',hex("+value+")) ELSE "+value+' END']
        chunks.append('json_object('+','.join(pairs)+')')
    # json_patch removes null-valued keys. Concatenate object bodies instead.
    return "'{' || "+" || ',' || ".join('substr('+c+',2,length('+c+')-2)' for c in chunks)+" || '}'"


class TaskConnection(sqlite3.Connection):
    backup_path=None
    backup_session=None
    backup_ready=False

    def raw(self,sql,args=()):return sqlite3.Connection.execute(self,sql,args)

    def cursor(self,factory=None):
        return sqlite3.Connection.cursor(self,factory=factory or (
            TaskCursor if self.backup_ready and policy_enabled(self.backup_path) else sqlite3.Cursor))

    def __enter__(self):return self

    def __exit__(self,error_type,error,traceback):
        if not self.backup_ready or not policy_enabled(self.backup_path):
            return sqlite3.Connection.__exit__(self,error_type,error,traceback)
        if error_type is None:self.commit()
        else:self.rollback()
        return False

    def backup(self,*args,**kwargs):
        if self.backup_ready and policy_enabled(self.backup_path):
            raise StorageError('TASK_BACKUP_DATABASE_COPY_REQUIRES_POOL')
        return sqlite3.Connection.backup(self,*args,**kwargs)

    def start_backup_transaction(self):
        if self.backup_session is not None or not self.backup_ready:return
        selected=manager_for(self.backup_path)
        if selected is None:return
        manager,batch_id=selected
        lock=manager.source_lock();lock.__enter__()
        try:
            state=manager.ensure_locked();manager.reconcile(state,self);manager.check(state,self)
            if self.in_transaction:raise StorageError('TASK_BACKUP_UNMANAGED_TRANSACTION_ALREADY_OPEN')
            self.install_journal()
            # DELETE on the TEMP journal starts sqlite3's implicit transaction.
            # Only TEMP objects have changed; close it before business BEGIN.
            sqlite3.Connection.commit(self)
            self.backup_session=(manager,state,batch_id,lock)
        except BaseException:
            lock.__exit__(None,None,None);raise

    def install_journal(self):
        self.raw('CREATE TEMP TABLE IF NOT EXISTS task_backup_delta(seq INTEGER PRIMARY KEY,table_name TEXT,key_json TEXT,before_json TEXT,after_json TEXT)')
        self.raw('DELETE FROM temp.task_backup_delta')
        tables=list(self.raw("SELECT name FROM main.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"))
        for number,(table,) in enumerate(tables):
            info=list(self.raw('PRAGMA main.table_info('+quote(table)+')'))
            columns=[row[1] for row in info]
            keys=[row[1] for row in sorted(info,key=lambda r:r[5]) if row[5]]
            # Without an explicit key, rowid is used for locating a row, but
            # the before/after objects contain only original columns.
            if not keys:
                keys=next(([k] for k in ('rowid','_rowid_','oid') if k not in columns),None)
                if not keys:raise StorageError('TASK_BACKUP_TABLE_HAS_NO_STABLE_KEY')
            for operation in ('INSERT','UPDATE','DELETE'):
                name='task_backup_'+str(number)+'_'+operation
                self.raw('DROP TRIGGER IF EXISTS temp.'+quote(name))
                alias='OLD' if operation=='DELETE' else 'NEW'
                before='NULL' if operation=='INSERT' else encoded(columns,'OLD')
                after='NULL' if operation=='DELETE' else encoded(columns,'NEW')
                sql=('CREATE TEMP TRIGGER '+quote(name)+' AFTER '+operation+' ON main.'+quote(table)+
                     ' BEGIN INSERT INTO task_backup_delta(table_name,key_json,before_json,after_json) VALUES ('+
                     "'"+table.replace("'","''")+"',"+encoded(keys,alias)+','+before+','+after+'); END')
                self.raw(sql)

    def execute(self,sql,parameters=()):
        if not self.backup_ready or not policy_enabled(self.backup_path):
            return sqlite3.Connection.execute(self,sql,parameters)
        keyword=first_keyword(sql)
        if keyword in ('COMMIT','END'):
            self.commit();return self.raw('SELECT 1 WHERE 0')
        if keyword=='ROLLBACK' and ' TO ' not in sql.upper():
            self.rollback();return self.raw('SELECT 1 WHERE 0')
        writing=keyword not in ('SELECT','PRAGMA','EXPLAIN','', 'RELEASE')
        if writing:
            self.start_backup_transaction()
            if self.backup_session and keyword in ('ATTACH','DETACH','VACUUM'):
                raise StorageError('TASK_BACKUP_DATABASE_COPY_REQUIRES_POOL')
            if self.backup_session and not self.in_transaction and keyword!='BEGIN':
                self.raw('BEGIN IMMEDIATE')
                manager,state,_,_=self.backup_session;manager.check(state,self)
        try:
            result=sqlite3.Connection.execute(self,sql,parameters)
            if keyword=='BEGIN' and self.backup_session:
                manager,state,_,_=self.backup_session;manager.check(state,self)
            return result
        except BaseException:
            # Caller retains the existing transaction/rollback contract.
            raise

    def executemany(self,sql,parameters):
        self.start_backup_transaction()
        if self.backup_session and not self.in_transaction:
            self.raw('BEGIN IMMEDIATE')
            manager,state,_,_=self.backup_session;manager.check(state,self)
        return sqlite3.Connection.executemany(self,sql,parameters)

    def executescript(self,script):
        if not self.backup_ready or not policy_enabled(self.backup_path):
            return sqlite3.Connection.executescript(self,script)
        # Do not let sqlite3.executescript implicitly commit behind the audit
        # hook. SQLite itself recognizes complete statements, including triggers.
        buffer='';result=None
        for char in script:
            buffer+=char
            if char==';' and sqlite3.complete_statement(buffer):
                result=self.execute(buffer);buffer=''
        if buffer.strip():result=self.execute(buffer)
        return result

    def changes(self):
        rows=[]
        for _,table,keys,before,after in self.raw('SELECT * FROM temp.task_backup_delta ORDER BY seq'):
            old=json.loads(before) if before is not None else None
            new=json.loads(after) if after is not None else None
            if old==new:continue
            pk=json.loads(keys)
            if old and new and any(k in old and old[k]!=new[k] for k in pk):
                raise StorageError('TASK_BACKUP_PRIMARY_KEY_MUTATION_REQUIRES_SEPARATE_TASK')
            values=new or old or {}
            fields=[]
            for field in sorted(set(old or {})|set(new or {})):
                previous=(old or {}).get(field);current=(new or {}).get(field)
                if old is None or new is None or previous!=current:
                    fields.append({'field':field,'before':previous,'after':current,
                                   'before_sha256':digest(previous),'after_sha256':digest(current)})
            item={'table':table,'key':pk,'sku':values.get('official_sku',values.get('sku')),
                  'before':old,'after':new,'fields':fields,
                  'before_sha256':digest(old),'after_sha256':digest(new)}
            if table=='localization_patch_events' and new and new.get('event_type')=='PATCH_APPLIED':
                patch=self.raw('SELECT * FROM localization_patches WHERE patch_id=?',(new['patch_id'],)).fetchone()
                approved=self.raw("SELECT * FROM localization_patch_events WHERE patch_id=? AND event_type='PATCH_APPROVED' ORDER BY rowid DESC LIMIT 1",(new['patch_id'],)).fetchone()
                item['patch']=dict(patch) if patch else None
                item['approval']=dict(approved) if approved else None
            rows.append(item)
        return rows

    def release_backup(self):
        session=self.backup_session;self.backup_session=None
        if session:session[3].__exit__(None,None,None)

    def commit(self):
        if not self.backup_session:return sqlite3.Connection.commit(self)
        manager,state,batch_id,_=self.backup_session;intent=None;committed=False
        try:
            intent=manager.prepare(state,self,batch_id=batch_id,changes=self.changes())
            self._commit_sqlite();committed=True
            if intent:manager.receipt(state,intent,committed=True)
        except BaseException as exc:
            if committed:
                raise PostCommitEvidenceError(exc,commit_id=intent['after_head'] if intent else state['expected_head'],
                                              intent=state.get('pending')) from exc
            sqlite3.Connection.rollback(self)
            if intent:
                # An I/O error around COMMIT is not proof of rollback. Inspect
                # actual HEAD/rows after rollback, retaining intent if unknown.
                try:_,outcome=manager.transaction_outcome(state,self)
                except Exception as uncertain:
                    exc.add_note('Durable intent retained; commit outcome not proven: '+str(uncertain))
                    raise exc
                if outcome=='COMMITTED':
                    try:manager.receipt(state,intent,committed=True,recovered=True)
                    except Exception as report_error:exc.add_note('Commit proven; receipt pending: '+str(report_error))
                    raise PostCommitEvidenceError(exc,commit_id=intent['after_head'],intent=state.get('pending')) from exc
                manager.receipt(state,intent,committed=False)
            raise
        finally:self.release_backup()

    def _commit_sqlite(self):return sqlite3.Connection.commit(self)

    def rollback(self):
        try:return sqlite3.Connection.rollback(self)
        finally:self.release_backup()

    def close(self):
        try:
            if self.backup_session:self.rollback()
        finally:sqlite3.Connection.close(self)


class TaskCursor(sqlite3.Cursor):
    """Keep cursor-based callers on the same transaction gate."""
    result=None

    def execute(self,sql,parameters=()):
        self.result=self.connection.execute(sql,parameters)
        self.result.row_factory=self.row_factory
        return self

    def executemany(self,sql,parameters):
        self.result=self.connection.executemany(sql,parameters);return self

    def executescript(self,sql):
        self.result=self.connection.executescript(sql);return self

    def fetchone(self):return self.result.fetchone() if self.result is not None else super().fetchone()
    def fetchall(self):return self.result.fetchall() if self.result is not None else super().fetchall()
    def fetchmany(self,size=None):
        return (self.result if self.result is not None else super()).fetchmany(self.arraysize if size is None else size)
    def __iter__(self):return iter(self.result) if self.result is not None else super().__iter__()
    def __next__(self):return next(self.result) if self.result is not None else super().__next__()
    @property
    def rowcount(self):return self.result.rowcount if self.result is not None else sqlite3.Cursor.rowcount.__get__(self)
    @property
    def lastrowid(self):return self.result.lastrowid if self.result is not None else sqlite3.Cursor.lastrowid.__get__(self)
    @property
    def description(self):return self.result.description if self.result is not None else sqlite3.Cursor.description.__get__(self)
    def close(self):
        if self.result is not None:self.result.close()
        return super().close()

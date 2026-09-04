-- Production identity: email + password + display name (was username + PIN).
-- Idempotent AND order-safe: migrations re-run on every startup in filename order, and 005 re-adds the
-- old `failed_pin_attempts` column ("add column if not exists") before this file runs — so the rename is
-- guarded on the NEW column not existing yet, and a re-added legacy column is simply dropped.
-- Legacy accounts keep working: their usernames become <username>@legacy.local so they stay reachable
-- through an email field, and their (short) PINs still verify (length is only enforced at registration).
do $$
begin
    if exists (select 1 from information_schema.columns where table_name = 'users' and column_name = 'username')
       and not exists (select 1 from information_schema.columns where table_name = 'users' and column_name = 'email') then
        alter table users rename column username to email;
    end if;
    if exists (select 1 from information_schema.columns where table_name = 'users' and column_name = 'pin_hash')
       and not exists (select 1 from information_schema.columns where table_name = 'users' and column_name = 'password_hash') then
        alter table users rename column pin_hash to password_hash;
    end if;
    if exists (select 1 from information_schema.columns where table_name = 'users' and column_name = 'failed_pin_attempts') then
        if not exists (select 1 from information_schema.columns where table_name = 'users' and column_name = 'failed_attempts') then
            alter table users rename column failed_pin_attempts to failed_attempts;
        else
            alter table users drop column failed_pin_attempts;   -- re-added by 005 on a later startup; dead
        end if;
    end if;
end $$;

alter table users add column if not exists display_name text;

update users set email = email || '@legacy.local' where position('@' in email) = 0;

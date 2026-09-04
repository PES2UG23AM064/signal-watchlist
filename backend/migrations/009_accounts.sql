-- Identity becomes email + password + display name (was username + PIN).
-- Order-safe re-run: 005 re-adds `failed_pin_attempts` before this file runs on every startup, so each
-- rename is guarded on the new column not existing yet, and a re-added legacy column is dropped.
-- Legacy accounts keep working: usernames become <username>@legacy.local and short PINs still verify
-- (length is only enforced at registration).
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
            alter table users drop column failed_pin_attempts;   -- re-added by 005 on a later startup
        end if;
    end if;
end $$;

alter table users add column if not exists display_name text;

update users set email = email || '@legacy.local' where position('@' in email) = 0;

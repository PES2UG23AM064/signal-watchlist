-- Slice 1 of the scoring engine: REAL daily-candle history + per-symbol baselines.
-- The candles are the raw material for (a) honest volatility/volume/52w baselines and (b) the
-- self-validating backtest (we run the exact scoring function over this real history). Cached in
-- Postgres so scoring survives Yahoo being down or rate-limited.

create table if not exists daily_candles (
    symbol  text not null,
    day     date not null,
    open    double precision,
    high    double precision,
    low     double precision,
    close   double precision not null,
    volume  bigint not null,
    primary key (symbol, day)          -- idempotent backfill / refresh
);

create table if not exists symbol_baselines (
    symbol           text primary key,
    last_close       double precision not null,
    ret_stdev_daily  double precision not null,  -- stdev of daily simple returns (the z denominator)
    avg_volume_20d   double precision not null,  -- volume-anomaly denominator
    week52_high      double precision not null,
    week52_low       double precision not null,
    beta             double precision,           -- vs ^NSEI via linear regression; null if not computable
    n_candles        integer not null,
    source           text not null default 'yahoo',
    computed_at      timestamptz not null default now()
);

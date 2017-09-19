CREATE OR REPLACE FUNCTION rand(max_val bigint) RETURNS bigint AS $$
BEGIN
    RETURN floor(random() * (max_val + 1));
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION rand_iv() RETURNS smallint AS 'SELECT rand(31)::smallint;' LANGUAGE SQL;

CREATE TYPE pokenature AS ENUM ('Hardy', 'Lonely', 'Brave', 'Adamant', 'Naughty', 'Bold', 'Docile', 'Relaxed', 'Impish', 'Lax', 'Timid', 'Hasty', 'Serious',
                                'Jolly', 'Naive', 'Modest', 'Mild', 'Quiet', 'Bashful', 'Rash', 'Calm', 'Gentle', 'Sassy', 'Careful', 'Quirky');
CREATE TYPE poketype AS ENUM ('Normal', 'Fighting', 'Flying', 'Poison', 'Ground', 'Rock', 'Bug', 'Ghost', 'Steel',
                              'Fire', 'Water', 'Grass', 'Electric', 'Psychic', 'Ice', 'Dragon', 'Dark', 'Fairy');
CREATE TYPE pokestat AS ENUM ('hp', 'attack', 'defense', 'sp_attack', 'sp_defense', 'speed');
CREATE TYPE pokeball AS ENUM ('Pokeball', 'Greatball', 'Ultraball', 'Masterball');

CREATE TABLE types (
    name poketype PRIMARY KEY,
    color integer,
    noeffect poketype[],
    effective poketype[],
    ineffective poketype[]
);

CREATE TABLE natures (
    mod smallint PRIMARY KEY,
    name pokenature,
    increase pokestat,
    decrease pokestat
);

CREATE TABLE pokenum (
    num smallint PRIMARY KEY
);

CREATE TABLE pokemon (
    num smallint REFERENCES pokenum(num),
    name text,
    form text,
    form_id smallint,
    generation smallint,
    type poketype[],
    legendary boolean,
    mythical boolean,
    hp smallint,
    attack smallint,
    defense smallint,
    sp_attack smallint,
    sp_defense smallint,
    speed smallint,
    xp_yield smallint,
    hp_yield smallint,
    attack_yield smallint,
    defense_yield smallint,
    sp_attack_yield smallint,
    sp_defense_yield smallint,
    speed_yield smallint
);

CREATE TABLE items (
    id smallserial,
    name text PRIMARY KEY,
    can_hold boolean,
    price smallint
);

CREATE TABLE rewards (
    name text REFERENCES items(name),
    num smallint
);

CREATE TABLE evolutions (
    id smallserial PRIMARY KEY,
    num smallint REFERENCES pokenum(num),
    prev smallint REFERENCES pokenum(num),
    next smallint REFERENCES pokenum(num),
    level smallint,
    item text REFERENCES items(name),
    trade boolean,
    trade_for smallint REFERENCES pokenum(num)
);

CREATE TABLE statistics (
    id bigserial PRIMARY KEY,
    event_name text,
    user_id bigint,
    message_id bigint,
    channel_id bigint,
    guild_id bigint,
    information json DEFAULT '{}',
    timestamp timestamp DEFAULT NOW()
);

CREATE TABLE moves (
    id smallint PRIMARY KEY,
    name text,
    type poketype REFERENCES types(name),
    category text,
    pp smallint,
    power smallint,
    accuracy smallint
);

CREATE TABLE trainers (
    user_id bigint PRIMARY KEY,
    secret_id integer DEFAULT rand(65535)::integer,
    inventory json DEFAULT '{"money": 1500, "Pokeball": 40, "Greatball": 10, "Ultraball": 5, "Masterball": 1}'
);

CREATE TABLE seen (
    user_id bigint REFERENCES trainers(user_id),
    num smallint REFERENCES pokenum(num),
    PRIMARY KEY (user_id, num)
);

CREATE TABLE found (
    id bigserial PRIMARY KEY,
    num smallint NOT NULL REFERENCES pokenum(num),
    name text,
    form_id smallint NOT NULL,
    ball pokeball NOT NULL,
    exp integer DEFAULT 0,
    item text REFERENCES items(name),
    party_position smallint,
    owner bigint REFERENCES trainers(user_id),
    original_owner bigint REFERENCES trainers(user_id) NOT NULL,
    moves text[] DEFAULT '{}' NOT NULL,
    personality bigint DEFAULT rand(4294967295),
    hp_iv smallint DEFAULT rand_iv(),
    attack_iv smallint DEFAULT rand_iv(),
    defense_iv smallint DEFAULT rand_iv(),
    sp_attack_iv smallint DEFAULT rand_iv(),
    sp_defense_iv smallint DEFAULT rand_iv(),
    speed_iv smallint DEFAULT rand_iv(),
    hp_ev smallint DEFAULT 0,
    attack_ev smallint DEFAULT 0,
    defense_ev smallint DEFAULT 0,
    sp_attack_ev smallint DEFAULT 0,
    sp_defense_ev smallint DEFAULT 0,
    speed_ev smallint DEFAULT 0
);

-- no more pokemon stuff

CREATE TABLE plonks (
    guild_id bigint,
    user_id bigint,
    PRIMARY KEY (guild_id, user_id)
);
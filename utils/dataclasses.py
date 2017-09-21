import math
import typing

from fuzzywuzzy import process

from utils.menus import STAR, GLOWING_STAR, ARROWS
from utils.errors import *


def xp_to_level(level: int):
    return (level ** 3) // 2


def level_from_xp(exp: int):
    level = int(((exp + 1) * 2) ** (1 / 3))
    if level == 0:
        level += 1
    return level


class Record:
    def __init__(self, ctx, rec):
        self.ctx = ctx
        self.__dict__.update(rec)


class Trainer(Record):
    @classmethod
    async def from_user_id(cls, ctx, user_id: int):
        try:
            query = ctx._trainer_from_user_id
        except AttributeError:
            query = ctx._trainer_from_user_id = await ctx.con.prepare("""
                INSERT INTO trainers (user_id) VALUES ($1)
                ON CONFLICT (user_id) DO UPDATE SET user_id=$1
                RETURNING *
                """)
        player_data = await query.fetchrow(user_id)
        c = cls(ctx, player_data)
        c.user = ctx.guild.get_member(user_id)

        return c

    async def set_inventory(self, inventory: dict):
        await self.ctx.con.execute("""
            UPDATE trainers SET inventory = $1 WHERE user_id = $2
            """, inventory, self.user_id)
        
        self.inventory = inventory

    async def get_pokemon(self, party=False, seen=False):
        if party:
            pokemon = await self.ctx.con.fetch("""
                SELECT * FROM found WHERE owner=$1 AND party_position IS NOT NULL ORDER BY party_position
                """, self.user_id)
        elif seen:
            pokemon = await self.ctx.con.fetch("""
                SELECT * FROM seen WHERE user_id=$1 ORDER BY num
                """, self.user_id)
            return [await Pokemon.from_num(self.ctx, p['num']) for p in pokemon]
        else:
            pokemon = await self.ctx.con.fetch("""
                SELECT * FROM found WHERE owner=$1 ORDER BY party_position, num, form_id, id
                """, self.user_id)

        return [await FoundPokemon.from_id(self.ctx, p['id']) for p in pokemon]

    async def see(self, pokemon: typing.Union['Pokemon', typing.List['Pokemon']]):
        if isinstance(pokemon, Pokemon):
            await self.ctx.con.execute("""
                INSERT INTO seen (user_id, num) VALUES ($1, $2)
                ON CONFLICT DO NOTHING
                """, self.user_id, pokemon.num)
        else:
            await self.ctx.con.executemany("""
                INSERT INTO seen (user_id, num) VALUES ($1, $2)
                ON CONFLICT DO NOTHING
                """, [(self.user_id, p.num) for p in pokemon])

    async def add_caught_pokemon(self, pokemon: 'Pokemon', ball):
        try:
            level_query = self.ctx._level_query
        except AttributeError:
            level_query = self.ctx._level_query = await self.ctx.con.prepare("""
              SELECT level FROM evolutions WHERE next = $1
              """)
        level = await level_query.fetchval(pokemon.num) or 0

        try:
            insert_query = self.ctx._insert_query
        except AttributeError:
            insert_query = self.ctx._insert_query = await self.ctx.con.prepare("""
                INSERT INTO found (num, form_id, ball, exp, owner, original_owner, personality) VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id
                """)

        found_id = await insert_query.fetchval(pokemon.num, pokemon.form_id, ball, xp_to_level(level), self.user_id,
                                               self.user_id, pokemon.personality)

        return await FoundPokemon.from_id(self.ctx, found_id)

    def __repr__(self):
        return f'<Trainer user_id={self.user_id}>'


class Pokemon(Record):
    @classmethod
    async def from_num(cls, ctx, num: int, form_id=0):
        try:
            query = ctx._pokemon_from_num
        except AttributeError:
            query = ctx._pokemon_from_num = await ctx.con.prepare("""
                SELECT *, (SELECT ARRAY(SELECT color FROM types WHERE types.name = ANY(type))) AS colors
                FROM pokemon WHERE num=$1 AND form_id=$2 LIMIT 1
                """)

        mon_data = await query.fetchrow(num, form_id)
        c = cls(ctx, mon_data)
        c.assign_extra_data()

        return c

    @classmethod
    async def from_name(cls, ctx, name: str, form_id=0):
        try:
            name_query = ctx._pokemon_names
        except AttributeError:
            name_query = ctx._pokemon_names = await ctx.con.prepare("""
                SELECT name FROM pokemon
                """)
        try:
            query = ctx._pokemon_from_name
        except AttributeError:
            query = ctx._pokemon_from_name = await ctx.con.prepare("""
                SELECT num FROM pokemon WHERE name=$1 AND form_id=$2
                """)

        names = [mon['name'] for mon in await name_query.fetch()]
        match, percent = process.extractOne(name, names)
        if percent < 70:
            raise PokemonNotFound(f'Pokemon not found with name: {name}')

        num = await query.fetchval(match, form_id)

        return cls.from_num(ctx, num)

    @classmethod
    async def random(cls, ctx, trainer):
        try:
            query = ctx._pokemon_random
        except AttributeError:
            query = ctx._pokemon_random = await ctx.con.prepare("""
            SELECT *, rand(4294967295) as personality,
            (SELECT ARRAY(SELECT color FROM types WHERE types.name = ANY(type))) AS colors
            FROM pokemon ORDER BY random() LIMIT 1
            """)

        mon = await query.fetchrow()

        c = cls(ctx, mon)
        c.assign_extra_data()
        c.shiny = await c.is_shiny(trainer=trainer)

        return c

    def assign_extra_data(self):
        self.color = self.get_color()
        self.star = self.get_star()
        if 'display_name' not in self.__dict__:
            if self.form:
                self.display_name = f'{self.form} {self.base_name}'
            else:
                self.display_name = self.base_name

    async def is_shiny(self, trainer=None):
        b = bin(self.personality)[2:].zfill(32)
        upper, lower = int(b[:16], 2), int(b[16:], 2)
        if trainer:
            original_trainer = trainer
        else:
            original_trainer = await Trainer.from_user_id(self.ctx, self.original_owner)
        return (((original_trainer.user_id % 65536) ^ original_trainer.secret_id)
                ^ (upper ^ lower)) <= int((65536 / 400))

    def get_star(self):
        return GLOWING_STAR if self.mythical else STAR if self.legendary else ''

    def get_color(self):
        return round(sum(self.colors) / len(self.colors))

    async def get_evolution_chain(self):
        chain = [await self.ctx.con.fetchrow("""
            SELECT prev, next,
            (SELECT base_name || (CASE WHEN mythical THEN $2 WHEN legendary THEN $3 ELSE '' END) FROM pokemon p WHERE p.num = e.num LIMIT 1) AS base_name
            FROM evolutions e WHERE num = $1
            """, self.num, GLOWING_STAR, STAR)]
        cur_ind = 0
        if chain[0]['prev'] is not None:
            chain.insert(0, await self.ctx.con.fetchrow("""
                SELECT prev,
                (SELECT base_name || (CASE WHEN mythical THEN $2 WHEN legendary THEN $3 ELSE '' END) FROM pokemon p WHERE p.num = e.num LIMIT 1) AS base_name
                FROM evolutions e WHERE next = $1
                """, self.num, GLOWING_STAR, STAR))
            cur_ind += 1
            if chain[0]['prev'] is not None:
                chain.insert(0, await self.ctx.con.fetchrow("""
                    SELECT base_name || (CASE WHEN mythical THEN $2 WHEN legendary THEN $3 ELSE '' END) AS base_name FROM pokemon WHERE num = $1 LIMIT 1
                    """, chain[0]['prev'], GLOWING_STAR, STAR))
                cur_ind += 1
        if chain[-1]['next'] is not None:
            chain.extend(await self.ctx.con.fetch("""
                SELECT
                (SELECT base_name || (CASE WHEN mythical THEN $2 WHEN legendary THEN $3 ELSE '' END) FROM pokemon p WHERE p.num = e.num LIMIT 1) AS base_name,
                (SELECT ARRAY(SELECT (SELECT base_name || (CASE WHEN mythical THEN $2 WHEN legendary THEN $3 ELSE '' END) AS base_name FROM pokemon p WHERE p.num = e2.num LIMIT 1)
                              FROM evolutions e2 WHERE e2.num = e.next)) AS next
                FROM evolutions e WHERE prev = $1
                """, self.num, GLOWING_STAR, STAR))
        if len(chain) == 1:
            return 'This Pokémon does not evolve.'
        start = '\N{BALLOT BOX WITH CHECK}'.join(r['base_name'] for r in chain[:cur_ind + 1])
        after = chain[cur_ind + 1:]
        chains = []
        if not after:
            chains.append(start)
        else:
            for m in after:
                m = dict(m)
                if not m['next']:
                    chains.append(ARROWS[1].join((start, m['base_name'])))
                else:
                    for name in m['next']:
                        chains.append(ARROWS[1].join((start, m['base_name'], name)))
        return '\n'.join(chains)

    def __repr__(self):
        return f'<Pokemon num={self.num} name={self.base_name}>'


class FoundPokemon(Pokemon):
    @classmethod
    async def from_owner(cls, ctx, trainer: Trainer):
        try:
            query = ctx._found_from_owner
        except AttributeError:
            query = ctx._found_from_owner = await ctx.con.prepare("""
                SELECT *, (SELECT ARRAY(SELECT color FROM types WHERE types.name = ANY(p.type))) AS colors
                FROM found f JOIN pokemon p ON
                f.num = p.num AND f.form_id = p.form_id
                WHERE owner=$1 ORDER BY f.party_position ASC, f.num, f.form_id, f.id
                """)

        data = await query.fetch(trainer.user_id)
        found_list = []
        for d in data:
            c = cls(ctx, d)
            await c.assign_extra_data()
            found_list.append(c)
        return found_list

    @classmethod
    async def from_num(cls, ctx, num: int, form_id=0):
        try:
            query = ctx._foundpokemon_from_num
        except AttributeError:
            query = ctx._foundpokemon_from_num = await ctx.con.prepare("""
                SELECT *, (SELECT ARRAY(SELECT color FROM types WHERE types.name = ANY(type))) AS colors
                FROM found WHERE num=$1 ORDER BY party_position ASC, num, form_id
                """)

        mon_data = await query.fetch(num)
        found_list = []
        for record in mon_data:
            c = cls(ctx, record)
            await c.assign_extra_data()
            found_list.append(c)

        return found_list

    @classmethod
    async def from_id(cls, ctx, found_id: int):
        try:
            query = ctx._found_from_id
        except AttributeError:
            query = ctx._found_from_id = await ctx.con.prepare("""
                SELECT * FROM found WHERE id=$1 ORDER BY party_position ASC, num, form_id
            """)

        found_data = await query.fetchrow(found_id)
        c = cls(ctx, found_data)
        await c.assign_extra_data()

        return c

    async def assign_extra_data(self):
        try:
            self.ctx._nature_query
        except AttributeError:
            self.ctx._nature_query = await self.ctx.con.prepare("""
                SELECT * FROM natures WHERE mod=$1
                """)
        base = await Pokemon.from_num(self.ctx, self.num, form_id=self.form_id)
        self.__dict__.update(base.__dict__)
        self.nature = await self.ctx._nature_query.fetchrow(self.personality % 25)
        self.shiny = await self.is_shiny()
        self.display_name = self._get_display_name()
        self.level = level_from_xp(self.exp)
        self.evolution_info = await self.get_evolution_info()
        self.stats = self._get_stats()
        super().assign_extra_data()

    def _get_display_name(self):
        if self.form is not None:
            name = f"{self.form} {self.base_name}"
        else:
            name = self.base_name
        if self.name is not None:
            name = f"{self.name} ({name})"
        return name

    async def transfer_ownership(self, new_trainer: typing.Union['Trainer', None]):
        try:
            query = self.ctx._transfer_ownership
        except AttributeError:
            query = self.ctx._transfer_ownership = await self.ctx.con.prepare("""
                UPDATE found SET owner=$1, party_position=NULL WHERE id=$2 AND owner=$3
            """)

        await query.fetch(new_trainer.user_id, self.id, self.owner)

    async def add_experience(self, amount: int):
        try:
            query = self.ctx._add_experience
        except AttributeError:
            query = self.ctx._add_experience = await self.ctx.con.prepare("""
                UPDATE found SET exp=exp+$1, num=$2 WHERE id=$3
                """)
        evolved = await self.check_evolve()
        if evolved:
            await query.fetch(amount, evolved, self.id)
            return await FoundPokemon.from_id(self.ctx, self.id)
        else:
            await query.fetch(amount, self.num, self.id)

        self.exp += amount
        return self

    async def check_evolve(self, trade_for: list=[], trading: bool=False):
        """Checks the evolve status for the specific Pokemon (row from found table).

        Parameters
        ----------
        trade_for: Optional[list]
            The list of Pokemon to check the trade_for.
        trading: Optional[bool]
            If the evolve check should require the Pokemon to be traded.

        Returns
        -------
        Union[int, None]:
            Will return the evolved Pokemon if the Pokemon can evolve,
            or returns None if the Pokemon cannot evolve.
        """
        evo_info = self.evolution_info
        to_evolve = None
        current_xp = self.exp
        for record in evo_info:
            if record['next'] is None:
                continue
            xp_to_evolve = xp_to_level(record['level'])

            if record['level'] == 1 or record['level'] == 100:
                if record['trade_for'] is not None and record['trade'] and trading:
                    if record['trade_for'] in [t['num'] for t in trade_for]:
                        to_evolve = record['next']
                        break
                elif record['trade'] == trading:
                    to_evolve = record['next']
                    break
                elif record['item'] == self.item:
                    to_evolve = record['next']
                    break
            else:
                if record['trade_for'] is not None and record['trade'] and trading and current_xp >= xp_to_evolve:
                    if record['trade_for'] in [t['num'] for t in trade_for]:
                        to_evolve = record['next']
                        break
                elif record['trade'] == trading and current_xp >= xp_to_evolve:
                    to_evolve = record['next']
                    break
                elif record['item'] == self.item and current_xp >= xp_to_evolve:
                    to_evolve = record['next']
                    break

        return to_evolve

    async def evolve(self, evolve_to: 'Pokemon'):
        try:
            query = self.ctx._do_evolve
        except AttributeError:
            query = self.ctx._do_evolve = await self.ctx.con.prepare("""
                UPDATE found SET num=$1 WHERE id=$2
                """)

        await query.fetch(evolve_to.num, self.id)

        return await FoundPokemon.from_id(self.id)

    async def get_evolution_info(self):
        try:
            query = self.ctx._evo_info
        except AttributeError:
            query = self.ctx._evo_info = await self.ctx.con.prepare("""
                SELECT * FROM evolutions WHERE num=$1
            """)

        return await query.fetch(self.num)

    def _get_stats(self):
        stat_dict = {}
        for stat in ['base_hp', 'base_attack', 'base_defense',
                     'base_sp_attack', 'base_sp_defense', 'base_speed']:
            stat = stat.replace('base_', '')
            base = math.floor((((2 * getattr(self, f'base_{stat}') + getattr(self, f'{stat}_iv') +
                                getattr(self, f'{stat}_ev') / 4)) * self.level) / 100) + 5
            if stat == 'hp':
                final = base + self.level + 5
            elif self.nature['increase'] == stat:
                final = base * 1.1
            elif self.nature['decrease'] == stat:
                final = base * 0.9
            else:
                final = base
            stat_dict[stat] = math.floor(final)

        return stat_dict

    async def update_ev(self, stat: str, value: int, add=True):
        if not stat.endswith('_ev'):
            stat += '_ev'
        current_value = await self.ctx.con.fetchval("""
            SELECT {} FROM found WHERE id=$1
            """.format(stat), self.id)
        if add:
            current_value += value
        else:
            current_value = value

        await self.ctx.con.execute("""
            UPDATE found SET {}=$1 WHERE id=$2
            """.format(stat), current_value, self.id)

    async def yield_stats(self, yield_from: 'FoundPokemon', participants=1, wild=False):
        evolved = None
        yield_from_info = await self.ctx.con.fetchrow("""
            SELECT * FROM pokemon WHERE num=$1
            """, yield_from['num'])
        for key, val in yield_from_info.items():
            if not key.endswith('_yield'):
                continue
            key = key.replace('_yield', '')
            if key == 'xp':
                wild_mod = 1 if wild else 1.5
                owner_mod = 1 if self.original_owner == self.owner else 1.5
                base_yield = val
                loser_level = level_from_xp(yield_from.exp)
                exp_amt = math.floor((wild_mod * owner_mod * base_yield * loser_level) / (7 * participants))
                evolved = await self.add_experience(exp_amt)
                continue
            await self.update_ev(self.ctx, key, val)
        return evolved

    async def set_name(self, name: str):
        try:
            query = self.ctx._set_name_query
        except AttributeError:
            query = self.ctx._set_name_query = await self.ctx.con.prepare("""
                UPDATE found SET name=$1 WHERE id=$2
                """)

        if name.lower() == self.base_name.lower():
            await query.fetch(None, self.id)
            self.name = self.base_name
        else:
            await query.fetch(name, self.id)
            self.name = name

        self.display_name = self.get_display_name()

    async def set_party_position(self, position: typing.Union[int, None]):
        try:
            query = self.ctx._set_party_pos
        except AttributeError:
            query = self.ctx._set_party_pos = await self.ctx.con.prepare("""
                UPDATE found SET party_position=$1 WHERE id=$2
                """)

        await query.fetch(position, self.id)
        self.party_position = position

    def __repr__(self):
        return f'<FoundPokemon id={self.id}, display_name={self.display_name}>'

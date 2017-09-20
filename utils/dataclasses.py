import math
import typing

from fuzzywuzzy import process

from cogs.pokemon import xp_to_level, level_from_xp
from utils.menus import STAR, GLOWING_STAR, ARROWS
from utils.errors import *


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
        c.user = await ctx.guild.get_member(user_id)

        return c

    async def set_inventory(self, inventory: dict):
        await self.ctx.con.execute("""
            UPDATE trainers SET inventory = $1 WHERE user_id = $2
            """, inventory, self.user_id)

    async def get_pokemon(self):
        return await self.ctx.con.fetch("""
            SELECT * FROM found WHERE owner=$1
            """, self.user_id)

    async def see(self, pokemon: typing.Union[Pokemon, typing.List[Pokemon]]):
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

    def __repr__(self):
        return f'<Trainer user_id={self.user_id}>'


class Pokemon(Record):
    def __init__(self, ctx, rec):
        self.color = self.get_color()
        self.star = self.get_star()
        self.display_name = self.get_display_name()
        super(Pokemon, self).__init__(ctx, rec)

    @classmethod
    async def from_num(cls, ctx, num: int, form_id=0):
        try:
            query = ctx._pokemon_from_num
        except AttributeError:
            query = ctx._pokemon_from_num = await ctx.con.prepare("""
                SELECT *, (SELECT ARRAY(SELECT color FROM types WHERE types.name = ANY(type))) AS colors
                FROM pokemon WHERE num=$1 AND form_id=$2 LIMIT 1
                """)

        mon_data = query.fetchrow(num, form_id)
        c = cls(ctx, mon_data)

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

    def get_display_name(self):
        if self.form is not None:
            name = f"{self.form} {self.base_name}"
        else:
            name = self.base_name
        if hasattr(self, 'name'):
            name = f"{self.name} ({name})"
        return name

    def get_star(self):
        return GLOWING_STAR if self.mythical else STAR if self.legendary else ''

    def get_color(self):
        return round(sum(self.colors) / len(self.colors))

    async def get_evolution_chain(self):
        chain = [await self.ctx.con.fetchrow("""
            SELECT prev, next,
            (SELECT name || (CASE WHEN mythical THEN $2 WHEN legendary THEN $3 ELSE '' END) FROM pokemon p WHERE p.num = e.num LIMIT 1) AS name
            FROM evolutions e WHERE num = $1
            """, self.num, GLOWING_STAR, STAR)]
        cur_ind = 0
        if chain[0]['prev'] is not None:
            chain.insert(0, await self.ctx.con.fetchrow("""
                SELECT prev,
                (SELECT name || (CASE WHEN mythical THEN $2 WHEN legendary THEN $3 ELSE '' END) FROM pokemon p WHERE p.num = e.num LIMIT 1) AS name
                FROM evolutions e WHERE next = $1
                """, self.num, GLOWING_STAR, STAR))
            cur_ind += 1
            if chain[0]['prev'] is not None:
                chain.insert(0, await self.ctx.con.fetchrow("""
                    SELECT name || (CASE WHEN mythical THEN $2 WHEN legendary THEN $3 ELSE '' END) AS name FROM pokemon WHERE num = $1 LIMIT 1
                    """, chain[0]['prev'], GLOWING_STAR, STAR))
                cur_ind += 1
        if chain[-1]['next'] is not None:
            chain.extend(await self.ctx.con.fetch("""
                SELECT
                (SELECT name || (CASE WHEN mythical THEN $2 WHEN legendary THEN $3 ELSE '' END) FROM pokemon p WHERE p.num = e.num LIMIT 1) AS name,
                (SELECT ARRAY(SELECT (SELECT name || (CASE WHEN mythical THEN $2 WHEN legendary THEN $3 ELSE '' END) AS name FROM pokemon p WHERE p.num = e2.num LIMIT 1)
                              FROM evolutions e2 WHERE e2.num = e.next)) AS next
                FROM evolutions e WHERE prev = $1
                """, self.num, GLOWING_STAR, STAR))
        if len(chain) == 1:
            return 'This Pokémon does not evolve.'
        start = '\N{BALLOT BOX WITH CHECK}'.join(r['name'] for r in chain[:cur_ind + 1])
        after = chain[cur_ind + 1:]
        chains = []
        if not after:
            chains.append(start)
        else:
            for m in after:
                m = dict(m)
                if not m['next']:
                    chains.append(ARROWS[1].join((start, m['name'])))
                else:
                    for name in m['next']:
                        chains.append(ARROWS[1].join((start, m['name'], name)))
        return '\n'.join(chains)

    def __repr__(self):
        return f'<Pokemon num={self.num} name={self.name}>'


class FoundPokemon(Pokemon):
    @classmethod
    async def from_owner(cls, ctx, trainer: Trainer):
        try:
            query = ctx._found_from_owner
        except AttributeError:
            query = ctx._found_from_owner = await ctx.con.prepare("""
                SELECT * FROM found f JOIN pokemon p ON
                    f.num = p.num AND f.form_id = p.form_id,
                (SELECT ARRAY(SELECT color FROM types WHERE types.name = ANY(type))) AS colors
                WHERE owner = $1
                """)

        data = await query.fetch(trainer.user_id)
        found_list = []
        for d in data:
            c = cls(ctx, d)
            await c.assign_extra_data()
            found_list.append(c)
        return found_list

    @classmethod
    async def from_id(cls, ctx, found_id: int):
        try:
            query = ctx._found_from_id
        except AttributeError:
            query = ctx._found_from_id = await ctx.con.prepare("""
                SELECT * FROM found
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
        self.nature = await self.ctx._nature_query.fetchrow(self.personality % 25)
        self.stats = self.get_stats()
        self.shiny = self.is_shiny()
        base = await Pokemon.from_num(self.ctx, self.num, form_id=self.form_id)
        self.__dict__.update(base.__dict__)


    async def is_shiny(self):
        b = bin(self.personality)[2:].zfill(32)
        upper, lower = int(b[:16], 2), int(b[16:], 2)
        original_trainer = Trainer.from_user_id(self.original_owner)
        return (((original_trainer.user_id % 65536) ^ original_trainer.secret_id)
                ^ (upper ^ lower)) <= int((65536 / 400))

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
        evo_info = await self.ctx.con.fetch("""
            SELECT * FROM evolutions WHERE num=$1
            """, self.num)
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

    def get_stats(self):
        level = level_from_xp(self.exp)
        stat_dict = {}
        for stat in ['base_hp', 'base_attack', 'base_defense',
                     'base_sp_attack', 'base_sp_defense', 'base_speed']:
            stat = stat.replace('base_', '')
            base = math.floor((((2 * getattr(self, f'base_{stat}') + getattr(self, f'{stat}_iv') +
                                getattr(self, f'{stat}_ev') / 4)) * level) / 100) + 5
            if stat == 'hp':
                final = base + level + 5
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

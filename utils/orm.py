from collections import Counter
import typing
import math

from fuzzywuzzy import process
import discord

from utils.menus import STAR, GLOWING_STAR, ARROWS
from utils.errors import PokemonNotFound


def xp_to_level(level: int):
    """Returns the amount of EXP needed for a level.

    Parameters
    ----------
    level: int
        The level to find the amount of EXP needed for.

    Returns
    -------
    int:
        The amount of EXP required for the level.
    """
    return (level ** 3) // 2


def level_from_xp(exp: int):
    """Returns the level for the specified amount of EXP.

    Parameters
    ----------
    exp: int
        The amount of EXP to find the level foor.

    Returns
    -------
    int:
        The level for the specified amount of EXP.
    """
    level = int(((exp + 1) * 2) ** (1 / 3))
    if level == 0:
        level += 1
    return level


async def get_all_pokemon(ctx):
    """Retrieve all stored :class:`Pokemon`.

    Parameters
    ----------
    ctx: discord.commands.Context
        The ctx used for connecting with the DB.

    Returns
    -------
    List[:class:`Pokemon`]:
        A list of all the stored :class:`Pokemon`.
    """
    pokemon = await ctx.con.fetch("""
        SELECT num FROM pokemon
        """)
    return [await Pokemon.from_num(ctx, p['num']) for p in pokemon]


class Record:
    """Represents a record from the DB in the form of an object.

    Parameters
    ----------
    ctx: discord.commands.Context
        The ctx used for connecting with the DB.
    rec: asyncpg.Record
        The record to create the object from.
    """
    def __init__(self, ctx, rec):
        self.ctx = ctx
        self.__dict__.update(rec)


class Trainer(Record):
    """Represents a row from the `trainers` table.

    Attributes
    ----------
    user_id: int
        The user ID of the :class:`Trainer`.
    secret_id: int
        The secret ID of the :class:`Trainer`.
    inventory: json
        The :class:`Trainer`'s inventory.
    """
    @classmethod
    async def from_user_id(cls, ctx, user_id: int):
        """Constructs a :class:`Trainer` from a user ID.

        Parameters
        ----------
        ctx: discord.commands.Context
            The ctx used for connecting with the DB.
        user_id: int
            The ID of the user to construct the :class:`Trainer` from.
        """
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
        if ctx.guild is not None:
            c.user = ctx.guild.get_member(user_id)
        else:
            c.user = discord.utils.get(list(ctx.bot.get_all_members()), id=user_id)
        c.inventory = Counter(c.inventory)

        return c

    async def set_inventory(self, inventory: dict):
        """Sets the inventory of the :class:`Trainer`.

        Parameters
        ----------
        inventory: json
            The inventory to replace the current :class:`Trainer`'s inventory.
        """
        inventory = +Counter(inventory)
        await self.ctx.con.execute("""
            UPDATE trainers SET inventory = $1 WHERE user_id = $2
            """, inventory, self.user_id)

        self.inventory = inventory

    async def get_pokemon(self, party=False, seen=False):
        """Retrieve all Pokemon of the :class:`Trainer`.

        Parameters
        ----------
        Optional[party: bool]
            Whether or not to retrieve only Pokemon in the :class:`Trainer`'s party.
        Optional[seen: bool]
            Whether or not to retrieve only Pokemon that the :class:`Trainer` has seen.

        Returns
        -------
        List[:class:`FoundPokemon`]:
            The list of Pokemon for the :class:`Trainer`.
        """
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
        """Mark a Pokemon or a list of Pokemon as seen.

        Parameters
        ----------
        pokemon: Union[:class:`Pokemon`, List[:class:`Pokemon]]
            The :class:`Pokemon` or list of :class:`Pokemon` to mark
            as seen for the :class:`Trainer`.
        """
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
        """Add a :class:`Pokemon` to the :class:`Trainer`'s pokemon.

        Parameters
        ----------
        pokemon: :class:`Pokemon`
            The :class:`Pokemon` to add to the :class:`Trainer`.
        ball: str
            The Pokeball that was used to catch the :class:`Pokemon`.

        Returns
        -------
        :class:`FoundPokemon`:
            The owned version of the :class:`Pokemon`.
        """
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
    """Represents a row from the `pokemon` table.

    Attributes
    ----------
    num: int
        The :class:`Pokemon`'s num.
    display_name: str
        A nicely formatted display name.
    color: int
        The color for the :class:`Pokemon`.
    star: str
        The unicode star for the :class:`Pokemon`.
    base_name: str
        The :class:`Pokemon` base name.
    form: str
        The name of the form for the :class:`Pokemon`.
    form_id: int
        The ID of the form for the :class:`Pokemon`.
    generation: int
        The generation that the :class:`Pokemon` is from.
    type: List[str]
        The types for the :class:`Pokemon`.
    legendary: bool
        Whether or not the :class:`Pokemon` is legendary.
    mythical: bool
        Whether or not the :class:`Pokemon` is mythical.
    base_hp: int
        The :class:`Pokemon`'s base HP.
    base_attack: int
        The :class:`Pokemon`'s base attack.
    base_defense: int
        The :class:`Pokemon`'s base defense.
    base_sp_attack: int
        The :class:`Pokemon`'s base SP. Attack.
    base_sp_defense: int
        The :class:`Pokemon`'s base SP. Defense.
    base_speed: int
        The :class:`Pokemon`'s base speed.
    xp_yield: int
        The XP this :class:`Pokemon` yields when losing battles.
    hp_yield: int
        The HP this :class:`Pokemon` yields when losing battles.
    attack_yield: int
        The attack this :class:`Pokemon` yields when losing battles.
    defense_yield: int
        The defense this :class:`Pokemon` yields when losing battles.
    sp_attack_yield: int
        The SP. Attack this :class:`Pokemon` yields when losing battles.
    sp_defense_yield: int
        The SP. Defense this :class:`Pokemon` yields when losing battles
    speed_yield: int
        The speed this :class:`Pokemon` yields when losing battles.
    """
    @classmethod
    async def from_num(cls, ctx, num: int, form_id=0):
        """Constructs a :class:`Pokemon` from a num.

        Parameters
        ----------
        ctx: discord.commands.Context
            The ctx to use when connecting with the DB.
        num: int
            The num to use when constructing.
        Optional[form_id: int]
            The form_id to also match when constructing.

        Returns
        -------
        :class:`Pokemon`:
            The constructed :class:`Pokemon` object.
        """
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
        """Constructs a :class:`Pokemon` from a name.

        Parameters
        ----------
        ctx: discord.commands.Context
            The ctx to use when connecting with the DB.
        name: str
            The name to use when constructing.
        Optional[form_id: int]
            The form_id to also match when constructing.

        Returns
        -------
        :class:`Pokemon`:
            The constructed :class:`Pokemon` object.
        """
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
        """Constructs a random :class:`Pokemon`.

        Parameters
        ----------
        ctx: discord.commands.Context
            The ctx to use when connecting with the DB.
        trainer: :class:`Trainer`
            The trainer that encountered the :class:`Pokemon`.
            Used for detecting if the :class:`Pokemon` is shiny.

        Returns
        -------
        :class:`Pokemon`:
            The constructed :class:`Pokemon` object.
        """
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
        return (((original_trainer.user_id % 65536) ^ original_trainer.secret_id) ^ (upper ^ lower)) <= int((65536 / 400))

    def get_star(self):
        return GLOWING_STAR if self.mythical else STAR if self.legendary else ''

    def get_color(self):
        return round(sum(self.colors) / len(self.colors))

    async def get_evolution_chain(self):
        """Returns a nicely formatted string of the :class:`Pokemon`'s evolution chain.

        Returns
        -------
        str:
            The string of the :class:`Pokemon`'s evolution chain.
        """
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
    """Represents a record from the `found` table.

    Attributes
    ----------
    id: int
        The ID of the record in the `found` table.
    name: str
        The custom name of the :class:`FoundPokemon`.
    ball: str
        The pokeball used to catch the :class:`FoundPokemon`.
    exp: int
        The amount of experience the :class:`FoundPokemon` has.
    level: int
        The :class:`FoundPokemon`'s level.
    item: str
        The item that the :class:`FoundPokemon` uses to evolve.
    party_position: Union[int, None]
        The party position of the :class:`FoundPokemon`. Can be
        `None` if the pokemon is not in a party.
    owner: int
        The :class:`FoundPokemon`'s current owner's ID.
    original_owner: int
        The :class:`FoundPokemon`'s original owner's ID.
    moves: Union[str, json]
        ???
    personality: int
        The :class:`FoundPokemon`'s personality.
    nature: str
        The :class:`FoundPokemon`'s nature.
    shiny: bool
        Whether or not the :class:`FoundPokemon` is shiny.
    evolution_info: List[asyncpg.Record]
        A list of evolution records for the :class:`FoundPokemon`.
    stats: dict
        A dictionary containing the :class:`FoundPokemon`'s statistics.
        Involves the calculations for IVs, EVs, and level.
    """
    @classmethod
    async def from_num(cls, ctx, num: int, form_id=0):
        """Constructs a list of :class:`FoundPokemon` using a given num.

        Parameters
        ----------
        ctx: discord.commands.Context
            The ctx to use when connecting with the DB.
        num: int
            The number to search for :class:`FoundPokemon`.
        Optional[form_id: int]
            The form_id to also match for.

        Returns
        -------
        List[:class:`FoundPokemon`]:
            A list of constructed :class:`FoundPokemon` objects.
        """
        try:
            query = ctx._foundpokemon_from_num
        except AttributeError:
            query = ctx._foundpokemon_from_num = await ctx.con.prepare("""
                SELECT *, (SELECT ARRAY(SELECT color FROM types WHERE types.name = ANY(types.name))) AS colors
                FROM found WHERE num=$1 ORDER BY party_position, num, form_id
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
        """Constructs a :class:`FoundPokemon` using a given ID.

        Parameters
        ----------
        ctx: discord.commands.Context
            The ctx to use when connecting with the DB.
        found_id: int
            The ID of the record in the `found` table.

        Returns
        -------
        :class:`FoundPokemon`:
            A constructed :class:`FoundPokemon` object.
        """
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
        self.evolution_info = await self.get_evolution_info()
        super().assign_extra_data()

    @property
    def display_name(self):
        if self.form is not None:
            name = f"{self.form} {self.base_name}"
        else:
            name = self.base_name
        if self.name is not None:
            name = f"{self.name} ({name})"
        return name

    @property
    def level(self):
        return level_from_xp(self.exp)

    async def transfer_ownership(self, new_trainer: typing.Union['Trainer', None]):
        try:
            query = self.ctx._transfer_ownership
        except AttributeError:
            query = self.ctx._transfer_ownership = await self.ctx.con.prepare("""
                UPDATE found SET owner=$1, party_position=NULL WHERE id=$2 AND owner=$3
            """)

        if new_trainer is None:
            await query.fetch(None, self.id, self.owner)
        else:
            await query.fetch(new_trainer.user_id, self.id, self.owner)

    async def add_experience(self, amount: int):
        """Adds experience to the :class:`FoundPokemon`.

        This will also check if the :class:`FoundPokemon` levels up
        and evolves, then proceeds to evolve the :class:`FoundPokemon`.

        Parameters
        ----------
        amount: int
            The amount of experience to add to the :class:`FoundPokemon`.

        Returns
        -------
        :class:`FoundPokemon`:
            This will either be the current :class:`FoundPokemon`, or the
            evolved :class:`FoundPokemon`.
        """
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
        """Checks the evolve status for :class:`FoundPokemon`.

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

        if to_evolve is None:
            return
        else:
            return await Pokemon.from_num(self.ctx, to_evolve)

    async def evolve(self, evolve_to: 'Pokemon'):
        """Evolves the :class:`FoundPokemon` to the specified :class:`Pokemon`.

        Parameters
        ----------
        evolve_to: :class:`Pokemon`
            The :class:`Pokemon` to evolve the :class:`FoundPokemon` to.

        Returns
        -------
        :class:`FoundPokemon`:
            The evolved :class:`FoundPokemon`.
        """
        try:
            query = self.ctx._do_evolve
        except AttributeError:
            query = self.ctx._do_evolve = await self.ctx.con.prepare("""
                UPDATE found SET num=$1 WHERE id=$2
                """)

        await query.fetch(evolve_to.num, self.id)

        return await FoundPokemon.from_id(self.ctx, self.id)

    async def get_evolution_info(self):
        try:
            query = self.ctx._evo_info
        except AttributeError:
            query = self.ctx._evo_info = await self.ctx.con.prepare("""
                SELECT * FROM evolutions WHERE num=$1
            """)

        return await query.fetch(self.num)

    @property
    def stats(self):
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
        """Yields statistics to the :class:`FoundPokemon` from another :class:`FoundPokemon`.

        This is used for battles.

        Parameters
        ----------
        yield_from: :class:`FoundPokemon`
            The :class:`FoundPokemon` to yield statistics from.
        Optional[participants: int]:
            The amount of participants in the battle.
        Optional[wild: bool]:
            Whether or not the battle was wild.

        Returns
        -------
        Union[int, None]:
            If the :class:`FoundPokemon` evolved, this returns the
            number of the :class:`Pokemon` to evolve to.
        """
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
        """Sets the name of the :class:`FoundPokemon`.

        Parameters
        ----------
        name: str
            The name to set for the :class:`FoundPokemon`.
            If this name is the base_name of the :class:`Pokemon`,
            this will reset the custom name to `None`.
        """
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

    async def set_party_position(self, position: typing.Union[int, None]):
        """Sets the party position of the :class:`FoundPokemon`.

        Parameters
        ----------
        position: Union[int, None]
            The party position to set to. `None` removes
            the :class:`FoundPokemon` from the party.
        """
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

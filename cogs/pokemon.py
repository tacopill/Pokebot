from random import randint
import itertools
import asyncio
import random
import math
import re

import aiohttp
import asyncpg
import discord
from discord.ext import commands
from fuzzywuzzy import process

import utils.statistics as stats_logger
from utils import errors, checks
from utils.menus import Menus, STAR, GLOWING_STAR, SPARKLES, SPACER, ARROWS, DONE, CANCEL
from utils.utils import wrap, unique

converter = commands.MemberConverter()


pokeballs = ('Pokeball', 'Greatball', 'Ultraball', 'Masterball')


def pokechannel():
    def check(ctx):
        if ctx.channel.name in ['pokemon']:
            return True
        raise errors.WrongChannel(discord.utils.get(ctx.guild.channels, name='pokemon'))
    return commands.check(check)


def xp_to_level(level):
    return (level ** 3) // 2


def level_from_xp(xp):
    level = int(((xp + 1) * 2) ** (1 / 3))
    if level == 0:
        level += 1
    return level


def catch(mon, ball):
    r = randint(1, 100)
    legendary = mon['legendary']
    mythical = mon['mythical']
    if (ball == 0 and r < (15 if mythical else 25 if legendary else 50)) \
            or (ball == 1 and r < (25 if mythical else 35 if legendary else 75)) \
            or (ball == 2 and r < (35 if mythical else 50 if legendary else 99)) \
            or (ball == 3 and r < (65 if mythical else 90 if legendary else 100)):
        return True
    return False


async def poke_converter(ctx, user_or_num):
    if user_or_num is None:
        return None
    try:
        return await converter.convert(ctx, user_or_num)
    except commands.BadArgument:
        try:
            return int(user_or_num)
        except ValueError:
            return user_or_num


def is_shiny(trainer: asyncpg.Record, personality: int):
    b = bin(personality)[2:].zfill(32)
    upper, lower = int(b[:16], 2), int(b[16:], 2)
    shiny = (((trainer['user_id'] % 65536) ^ trainer['secret_id']) ^ (upper ^ lower)) <= (65536 / 400)
    return SPARKLES if shiny else ''


def get_star(mon: asyncpg.Record):
    return GLOWING_STAR if mon['mythical'] else STAR if mon['legendary'] else ''


def get_name(mon: asyncpg.Record):
    """mon argument must have:
        name      : custom name
        base_name : pokemon's name
        form      : form name

       Returns:
       Speed Deoxys
       Sonic (Speed Deoxys)
    """
    if mon['form'] is not None:
        name = f"{mon['form']} {mon['base_name']}"
    else:
        name = mon['base_name']
    if mon['name']:
        name = f"{mon['name']} ({name})"
    return name


async def get_pokemon_color(ctx, num=0, *, mon: asyncpg.Record=None):
    if num:
        mon = await ctx.con.fetch('''
            SELECT type FROM pokemon WHERE num = $1 AND form_id = 0
            ''', num)
    if mon is not None:
        colors = await ctx.con.fetch('''
            SELECT color FROM types WHERE name = ANY($1)''', mon['type'])
        return round(sum(color['color'] for color in colors) / len(colors))
    return 0


async def set_inventory(ctx, uid, inv):
    return await ctx.con.execute('''
        UPDATE trainers SET inventory = $1 WHERE user_id = $2
        ''', inv, uid)


async def get_found_counts(ctx, uid):
    return await ctx.con.fetch('''
        SELECT num, form_id, COUNT(*) AS count,
        (SELECT name || (CASE WHEN mythical THEN '$2' WHEN legendary THEN '$3' ELSE '' END) FROM pokemon WHERE pokemon.num = found.num LIMIT 1),
        (SELECT form FROM pokemon WHERE pokemon.num = found.num AND pokemon.form_id = found.form_id)
        FROM found WHERE owner = $1 GROUP BY num, form_id ORDER BY num, form_id
        ''', uid, GLOWING_STAR, STAR)


async def see(ctx, uid, num):
    """num can be int or list"""
    if isinstance(num, int):
        await ctx.con.execute("""
                      INSERT INTO seen (user_id, num) VALUES ($1, $2)
                      ON CONFLICT DO NOTHING
                      """, uid, num)
    else:
        await ctx.con.executemany("""
                      INSERT INTO seen (user_id, num) VALUES ($1, $2)
                      ON CONFLICT DO NOTHING
                      """, [(uid, n) for n in num])


async def get_rewards(ctx):
    return await ctx.con.fetch('''
        SELECT * FROM rewards
        ''')


async def get_evolution_chain(ctx, num):
    chain = [await ctx.con.fetchrow('''
        SELECT prev, next,
        (SELECT name || (CASE WHEN mythical THEN $2 WHEN legendary THEN $3 ELSE '' END) FROM pokemon p WHERE p.num = e.num LIMIT 1) AS name
        FROM evolutions e WHERE num = $1
        ''', num, GLOWING_STAR, STAR)]
    cur_ind = 0
    if chain[0]['prev'] is not None:
        chain.insert(0, await ctx.con.fetchrow('''
            SELECT prev,
            (SELECT name || (CASE WHEN mythical THEN $2 WHEN legendary THEN $3 ELSE '' END) FROM pokemon p WHERE p.num = e.num LIMIT 1) AS name
            FROM evolutions e WHERE next = $1
            ''', num, GLOWING_STAR, STAR))
        cur_ind += 1
        if chain[0]['prev'] is not None:
            chain.insert(0, await ctx.con.fetchrow('''
                SELECT name || (CASE WHEN mythical THEN $2 WHEN legendary THEN $3 ELSE '' END) AS name FROM pokemon WHERE num = $1 LIMIT 1
                ''', chain[0]['prev'], GLOWING_STAR, STAR))
            cur_ind += 1
    if chain[-1]['next'] is not None:
        chain.extend(await ctx.con.fetch('''
            SELECT
            (SELECT name || (CASE WHEN mythical THEN $2 WHEN legendary THEN $3 ELSE '' END) FROM pokemon p WHERE p.num = e.num LIMIT 1) AS name,
            (SELECT ARRAY(SELECT (SELECT name || (CASE WHEN mythical THEN $2 WHEN legendary THEN $3 ELSE '' END) AS name FROM pokemon p WHERE p.num = e2.num LIMIT 1)
                          FROM evolutions e2 WHERE e2.num = e.next)) AS next
            FROM evolutions e WHERE prev = $1
            ''', num, GLOWING_STAR, STAR))
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


async def get_player(ctx, uid):
    player_data = await ctx.con.fetchrow("""
                                INSERT INTO trainers (user_id) VALUES ($1)
                                ON CONFLICT (user_id) DO UPDATE SET user_id=$1
                                RETURNING *
                                """, uid)
    return player_data


async def get_player_pokemon(ctx, uid):
    player_pokemon = await ctx.con.fetch("""
                                   SELECT * FROM found WHERE owner=$1
                                   """, uid)
    return player_pokemon


async def add_experience(ctx, mon: asyncpg.Record, amount: int):
    current_exp = mon['exp']
    current_exp += amount

    await ctx.con.execute("""
                  UPDATE found SET exp=$1 WHERE id=$2
                  """, current_exp, mon['id'])

    evolved = await check_evolve(ctx, mon)
    if evolved is not None:
        await ctx.con.execute("""
                      UPDATE found SET num=$1 WHERE id=$2
                      """, evolved, mon['id'])


async def update_ev(ctx, stat: str, value: int, mon: asyncpg.Record, add=True):
    if not stat.endswith('_ev'):
        stat += '_ev'
    current_value = await ctx.con.fetchval("""
                                  SELECT {} FROM found WHERE id=$1
                                  """.format(stat), mon['id'])
    if add:
        current_value += value
    else:
        current_value = value

    await ctx.con.execute("""
                  UPDATE found SET {}=$1 WHERE id=$2
                  """.format(stat), current_value, mon['id'])


async def yield_stats(ctx, yield_from: asyncpg.Record, yield_to: asyncpg.Record, participants=1, wild=False):
    """yield_from is a row from the found table, and yield_to is a row from the
    found table."""
    evolved = None
    yield_from_info = await ctx.con.fetchrow("""
                                    SELECT * FROM pokemon WHERE num=$1
                                    """, yield_from['num'])
    for key, val in yield_from_info.items():
        if not key.endswith('_yield'):
            continue
        key = key.replace('_yield', '')
        if key == 'xp':
            wild_mod = 1 if wild else 1.5
            owner_mod = 1 if yield_to['original_owner'] == yield_to['owner'] else 1.5
            base_yield = val
            loser_level = level_from_xp(yield_from['exp'])
            exp_amt = math.floor((wild_mod * owner_mod * base_yield * loser_level)/(7 * participants))
            evolved = await add_experience(ctx, yield_to, exp_amt)
            continue
        await update_ev(ctx, key, val, yield_to)
    return evolved

async def get_pokemon(ctx, num, form_id=0):
    mon_info = await ctx.con.fetchrow("""
                             SELECT * FROM pokemon WHERE num=$1 AND form_id=$2
                             """, num, form_id)
    return mon_info


async def check_evolve(ctx, mon: asyncpg.Record, trade_for: list=[], trading: bool=False):
    """Checks the evolve status for the specific Pokemon (row from found table).

    Parameters
    ----------
    mon: asyncpg.Record
        The Pokemon to check the evolve status for.
    trade_for: Optional[list]
        The list of Pokemon to check the trade_for.
    trading: Optional[bool]
        If the evolve check should require the Pokemon to be traded.

    Returns
    -------
    Union[int, None]:
        Will return the evolved Pokemon's num if the Pokemon can evolve,
        or returns None if the Pokemon cannot evolve.
    """
    evo_info = await ctx.con.fetch("""
                             SELECT * FROM evolutions WHERE num=$1
                             """, mon['num'])
    to_evolve = None
    current_xp = mon['exp']
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
            elif record['item'] == mon['item']:
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
            elif record['item'] == mon['item'] and current_xp >= xp_to_evolve:
                to_evolve = record['next']
                break

    return to_evolve


async def get_pokemon_stats(ctx, found):
    mon = await get_pokemon(ctx, found['num'], form_id=found['form_id'])
    nature = await ctx.con.fetchrow("""
                           SELECT * FROM natures WHERE mod=$1
                           """, found['personality'] % 25)
    level = level_from_xp(found['exp'])
    stat_dict = {}
    for stat in ['hp', 'attack', 'defense', 'sp_attack', 'sp_defense', 'speed']:
        base = math.floor(((2 * mon[stat] + found[f'{stat}_iv'] + (found[f'{stat}_ev']/4)) * level)/100) + 5
        if stat == 'hp':
            final = base + level + 5
        elif nature['increase'] == stat:
            final = base * 1.1
        elif nature['decrease'] == stat:
            final = base * 0.9
        else:
            final = base
        stat_dict[stat] = math.floor(final)

    return stat_dict


async def is_legendary(ctx, num):
    legendary = await ctx.con.fetchval("""
                               SELECT EXISTS(SELECT * FROM pokemon WHERE num=$1 AND legendary AND NOT mythical)
                               """, num)
    return legendary


async def is_mythical(ctx, num):
    mythical = await ctx.con.fetchval("""
                              SELECT EXISTS(SELECT * FROM pokemon WHERE num=$1 and mythical)
                              """, num)
    return mythical


class Pokemon(Menus):
    def __init__(self, bot):
        self.bot = bot
        self.image_path = 'data/pokemon/images/{}/{}-{}.gif'
        self.max_party_size = 4


###################
#                 #
# POKEMON         #
#                 #
###################

    @checks.db
    @commands.group(invoke_without_command=True, aliases=['pokemen', 'pokermon', 'digimon'])
    @commands.cooldown(1, 150, commands.BucketType.user)
    @pokechannel()
    async def pokemon(self, ctx):
        """Gives you a random Pokemon!"""
        player_name = ctx.author.name
        player_id = ctx.author.id
        mon = await ctx.con.fetchrow('''
            SELECT num, name, form, form_id, type, legendary, mythical, rand(4294967295) as personality,
            (SELECT form FROM pokemon p2 WHERE p2.num = pokemon.num AND p2.form_id = 0) AS base_form,
            (SELECT ARRAY(SELECT color FROM types WHERE types.name = ANY(type))) AS colors
            FROM pokemon ORDER BY random() LIMIT 1''')
        trainer = await get_player(ctx, player_id)
        star = get_star(mon)
        shiny = is_shiny(trainer, mon['personality'])
        await stats_logger.log_event(ctx, 'pokemon_encountered', pokemon_num=mon['num'], shiny=bool(shiny))
        if shiny:
            if mon['base_form']:
                form = mon['base_form'] + ' '
            else:
                form = ''
            form_id = 0
        else:
            if mon['form']:
                form = mon['form'] + ' '
            else:
                form = ''
            form_id = mon['form_id']
        inv = trainer['inventory']
        balls = [self.bot.get_emoji_named(ball) for ball in pokeballs if inv.get(ball)]
        embed = discord.Embed(description=f'A wild **{form}{mon["name"]}**{star}{shiny} appears!' +
                              (f'\nUse a {balls[0]} to catch it!' if balls else ''))
        embed.color = await get_pokemon_color(ctx, mon=mon)
        embed.set_author(icon_url=ctx.author.avatar_url, name=player_name)
        embed.set_image(url='attachment://pokemon.gif')
        msg = await ctx.send(embed=embed, file=discord.File(self.image_path.format('normal', mon['num'], 0),
                                                            filename='pokemon.gif'))
        await see(ctx, player_id, mon['num'])
        catch_attempts = 0
        while catch_attempts <= 2:
            trainer = await get_player(ctx, player_id)
            inv = trainer['inventory']
            balls = [self.bot.get_emoji_named(ball) for ball in pokeballs if inv.get(ball)]
            can_react_with = [*balls, CANCEL]
            for emoji in can_react_with:
                await msg.add_reaction(emoji)
            try:
                def check(reaction, user):
                    return (reaction.emoji in can_react_with and
                            reaction.message.id == msg.id and
                            user == ctx.author)
                reaction, _ = await self.bot.wait_for('reaction_add', check=check, timeout=20)

                await stats_logger.log_event(ctx, 'item_used', item=reaction.emoji.name)
            except asyncio.TimeoutError:
                embed.description = f'**{form}{mon["name"]}**{star}{shiny} escaped because you took too long! :stopwatch:'
                await msg.edit(embed=embed, delete_after=60)
                await msg.clear_reactions()
                return
            await msg.clear_reactions()
            if reaction.emoji in balls:
                if catch(mon, balls.index(reaction.emoji)):
                    embed.description = wrap(f'You caught **{form}{mon["name"]}**{star}{shiny} successfully!',
                                             reaction.emoji)
                    await msg.edit(embed=embed, delete_after=60)
                    level = await ctx.con.fetchval('''
                        SELECT level FROM evolutions WHERE next = $1
                        ''', mon['num']) or 0
                    async with ctx.con.transaction():
                        found_id = await ctx.con.fetchval('''
                            INSERT INTO found (num, form_id, ball, exp, owner, original_owner, personality) VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id
                            ''', mon['num'], form_id, reaction.emoji.name, xp_to_level(level), player_id, player_id, mon['personality'])
                    await stats_logger.log_event(ctx, 'pokemon_caught', attempts=catch_attempts+1, ball=reaction.emoji.name,
                                          id=found_id)
                    break
                else:
                    escape_quotes = ['Oh no! The Pokémon broke free!', 'Aww... It appeared to be caught!',
                                     'Aargh! Almost had it!', 'Gah! It was so close, too!']
                    embed.description = random.choice(escape_quotes)
                inv[reaction.emoji.name] -= 1
                await set_inventory(ctx, player_id, inv)
                catch_attempts += 1
                await msg.edit(embed=embed)
            else:
                embed.description = wrap(f'You ran away from **{form}{mon["name"]}**{star}{shiny}!', ':chicken:')
                await msg.edit(embed=embed, delete_after=60)
                await stats_logger.log_event(ctx, 'pokemon_fled', catch_attempts=catch_attempts+1,
                                             pokemon_num=mon['num'], shiny=bool(shiny))
                break
        else:
            embed.description = f'**{form}{mon["name"]}**{star}{shiny} has escaped!'
            await stats_logger.log_event(ctx, 'pokemon_fled', catch_attempts=catch_attempts+1,
                                         pokemon_num=mon['num'], shiny=bool(shiny))
            await msg.edit(embed=embed, delete_after=60)

###################
#                 #
# PC              #
#                 #
###################

    @checks.db
    @commands.group(invoke_without_command=True)
    @pokechannel()
    async def pc(self, ctx, *, member: discord.Member = None):
        """Opens your PC."""
        await stats_logger.log_event(ctx, 'pc_accessed')
        member = member or ctx.author

        total_pokemon = await ctx.con.fetchval("""
                                      SELECT COUNT(DISTINCT num) FROM pokemon
                                      """)
        found = await ctx.con.fetch("""
                              WITH p AS (SELECT num, name, form, form_id, legendary, mythical FROM pokemon)
                              SELECT f.num, f.name, f.id, original_owner, party_position, personality, p.name AS base_name, p.form, legendary, mythical FROM found f
                              JOIN p ON p.num = f.num AND p.form_id = f.form_id
                              WHERE owner = $1 ORDER BY f.party_position, f.num, f.form_id;
                              """, member.id)
        total_found = len(found)
        remaining = total_pokemon - total_found

        legendaries = await ctx.con.fetchval("""
                                    SELECT COUNT(*) FROM found WHERE owner=$1 AND num=ANY((SELECT num FROM pokemon WHERE legendary=True))
                                    """, member.id)
        mythics = await ctx.con.fetchval("""
                                SELECT COUNT(*) FROM found WHERE owner=$1 AND num=ANY((SELECT num FROM pokemon WHERE mythical=True))
                                """, member.id)

        header = f"__**{member.name}'s PC**__"
        if total_found == 0:
            header += " __**is empty.**__"
        if total_found == 0:
            return await ctx.send(header, delete_after=60)
        spacer = SPACER * 21

        key = f'{ARROWS[0]} Click to go back a page.\n{ARROWS[1]} Click to go forward a page.\n{CANCEL}' \
              f' Click to exit your pc.'

        counts = wrap(f'**{total_found}** collected out of {total_pokemon} total Pokemon. {remaining} left to go!'
                      f'\n**{total_found - mythics - legendaries}** Normal | **{legendaries}** Legendary {STAR}'
                      f' | **{mythics}** Mythical {GLOWING_STAR}', spacer, sep='\n')

        header = '\n'.join([header, 'Use **!pokedex** to see which Pokémon you\'ve encountered!\nUse **!pokedex** ``#`` to take a closer look at a Pokémon!', key, counts])

        trainers = {t['user_id']: t for t in await ctx.con.fetch("""
                                                           SELECT * FROM trainers WHERE user_id = ANY($1)
                                                           """, set(m['original_owner'] for m in found))}
        options = []
        done = []
        for mon in found:
            if get_name(mon) in done and mon['party_position'] is None:
                continue
            if mon['party_position'] is None:
                mon_count = sum(get_name(m) == get_name(mon) for m in found if m['party_position'] is None)
                done.append(get_name(mon))
            elif mon['party_position'] is not None:
                mon_count = 1
            shiny = is_shiny(trainers[mon['original_owner']], mon['personality'])
            shiny = SPARKLES if shiny else ''
            count = f" x{mon_count}" if mon_count > 1 else ''
            name = get_name(mon)
            options.append("{} **{}.** {}{}{}{}".format('\📍' if mon['party_position'] is not None else '',
                                                        mon['num'], name, get_star(mon), shiny, count))
        await self.reaction_menu(options, ctx.author, ctx.channel, 0, per_page=20, code=False, header=header)

    async def get_pc_info_embed(self, ctx, mon):
        pokedex = self.bot.get_emoji_named('Pokedex')
        em = discord.Embed()
        info = await get_pokemon(ctx, mon['num'], form_id=mon['form_id'])
        em.color = await get_pokemon_color(ctx, mon=info)
        mon_dict = dict(mon)
        mon_dict['base_name'] = info['name']
        mon_dict['form'] = info['form']
        name = get_name(mon_dict)
        level = level_from_xp(mon['exp'])
        needed_xp = xp_to_level(level+1) - xp_to_level(level)
        current_xp = mon['exp'] - xp_to_level(level)
        bar_length = 10
        FILLED_BAR = '■'
        UNFILLED_BAR = '□'
        bar = f'[{UNFILLED_BAR * bar_length}]()'
        percent_needed = (current_xp / needed_xp)
        filled_bars = int(bar_length * percent_needed)
        if filled_bars != 0:
            bar = f"[{(FILLED_BAR * filled_bars).ljust(bar_length, UNFILLED_BAR)}]()"

        em.description = wrap(f'__Your {name}{get_star(info)}\'s Information__', pokedex)
        em.description += f"\n**ID:** {mon['num']}\n" \
                          f"**Level:** {level}\n" \
                          f"**EXP:** {current_xp}/{needed_xp}\n{bar}\n" \
                          f"**Type:** {' & '.join(info['type'])}\n" \
                          f"**Caught Using:** {self.bot.get_emoji_named(mon['ball'])}\n"

        if mon['party_position'] != None:
            em.description += f"**Party Position**: {mon['party_position'] + 1}"

        trainer = await get_player(ctx, ctx.author.id)
        personality = mon['personality']
        shiny_status = 'shiny' if is_shiny(trainer, personality) else 'normal'
        image = self.image_path.format(shiny_status, mon['num'], 0) # replace 0 with mon['form_id'] to support forms

        stats = await get_pokemon_stats(ctx, mon)
        em.add_field(name='Statistics', value='\n'.join(f"**{stat.replace('_', '. ').title()}**: {val}"
                                                        for stat, val in stats.items()))
        evo = await get_evolution_chain(ctx, mon['num'])
        em.add_field(name='Evolutions', value=evo, inline=False)
        return em, image

    @checks.db
    @pc.command(name='info')
    @pokechannel()
    async def pc_info(self, ctx, *, query: str):
        """Display information for a specific Pokemon from the user's PC."""
        if query.isdigit():  # If the user enters a Pokemon's number.
            query_type = 'by_num'
            query = int(query)
            mon_list = await ctx.con.fetch("""
                                     SELECT * FROM found WHERE owner=$1 AND num=$2 ORDER BY party_position, num, form_id
                                      """, ctx.author.id, query)
        elif any(comparator in query for comparator in ['<', '>', '=']):  # Advanced query
            valid_stats = ['hp', 'attack', 'defense', 'sp_attack', 'sp_defense', 'speed']
            query_type = 'by_statistic'

            def query_repl(match):
                if match.group(1) not in valid_stats or not match.group(3).isdigit() or \
                                match.group(2) not in ['>', '<', '=']:
                    return ''
                return f'{match.group(1)} {match.group(2)} {match.group(3)}'

            result = re.sub(r"([^\s]+)\s*?([<>=])\s*?([^\s]+)", query_repl, query)
            if not result:
                return await ctx.send('Invalid query. The value must be a number. '
                                      '\nValid statistics include: `{}`'.format(', '.join(valid_stats)))
            stat, comparator, value = result.split(' ')
            if comparator == '=':
                comparator += '='
            found = await ctx.con.fetch("""
                                  SELECT * FROM found WHERE owner=$1 GROUP BY num, form_id, id
                                  """, ctx.author.id)
            mon_list = []
            for record in found:
                stats = await get_pokemon_stats(ctx, record)
                if eval(f"""{stats[stat]} {comparator} {value}"""):
                    mon_list.append(record)
            if not mon_list:
                return await ctx.send(f'Pokemon with `{result}` does not exist.', delete_after=60)
        else:  # Fuzzy match the query with all the Pokemon in the user's PC.
            query_type = 'by_fuzzy'
            pokemon_names = await ctx.con.fetch("""
                                          SELECT name FROM pokemon WHERE num=ANY(SELECT num FROM found WHERE owner=$1)
                                          """, ctx.author.id)
            pokemon_names = [mon['name'] for mon in pokemon_names]
            result = process.extractOne(query, pokemon_names)
            if result[1] < 70:
                return await ctx.send(f'Pokemon {query} does not exist.', delete_after=60)
            pokemon = await ctx.con.fetch("""
                                           SELECT * FROM found WHERE owner=$1 AND num=ANY(SELECT num FROM pokemon WHERE name=$2) ORDER BY party_position, num, form_id
                                           """, ctx.author.id, result[0])
            mon_list = pokemon

        await stats_logger.log_event(ctx, 'pc_accessed', query=query, query_type=query_type)

        if len(mon_list) == 1:
            embed, im = await self.get_pc_info_embed(ctx, mon_list[0])
            embed.set_image(url='attachment://pokemon.gif')
            chosen_mon = mon_list[0]
        else:
            mon_dict = [dict(mon) for mon in mon_list]
            mon_names = []
            for mon in mon_dict:
                pokemon = await ctx.con.fetchrow("""
                                        SELECT name, form FROM pokemon WHERE num=$1
                                        """, mon['num'])
                mon['base_name'] = pokemon['name']
                mon['form'] = pokemon['form']
                mon['mythical'] = await is_mythical(ctx, mon['num'])
                mon['legendary'] = await is_legendary(ctx, mon['num'])
                mon_names.append("{} **{}.** {}{}".format('\📍' if mon['party_position'] is not None else '',
                                                          mon['num'], get_name(mon), get_star(mon)))

            header = f"__**{ctx.author.name}'s PC**__"
            selected = await self.reaction_menu(mon_names, ctx.author, ctx.channel, 1, per_page=10, code=False,
                                                header=header, return_from=mon_list)
            if not selected:
                return

            embed, im = await self.get_pc_info_embed(ctx, selected[0])
            embed.set_image(url='attachment://pokemon.gif')
            chosen_mon = selected[0]

        msg = await ctx.send(embed=embed, file=discord.File(im, filename='pokemon.gif'), delete_after=120)
        while True:
            chosen_mon = await ctx.con.fetchrow("""
                                       SELECT * FROM found WHERE id=$1
                                       """, chosen_mon['id'])
            party = await ctx.con.fetch("""
                                  SELECT * FROM found WHERE party_position IS NOT NULL and owner=$1 ORDER BY party_position ASC
                                  """, ctx.author.id)
            em, im = await self.get_pc_info_embed(ctx, chosen_mon)
            em.set_image(url='attachment://pokemon.gif')
            await msg.edit(embed=em, delete_after=120)
            await msg.add_reaction('\N{PENCIL}')
            up_rxn = None
            down_rxn = None

            evo_info = await ctx.con.fetch("""
                                     SELECT * FROM evolutions WHERE num=$1 AND item IS NOT NULL
                                     """, chosen_mon['num'])
            evo_dict = {info['item']: [info['next'], self.bot.get_emoji_named(info['item'])] for info in evo_info}
            trainer = await get_player(ctx, ctx.author.id)
            for key, val in evo_dict.items():
                if key in trainer['inventory']:
                    await msg.add_reaction(val[1])

            if chosen_mon['party_position'] is not None:
                await msg.add_reaction('\N{CROSS MARK}')
                cur_index = party.index(chosen_mon)
                try:
                    if party[cur_index+1]:
                        await msg.add_reaction(ARROWS[2])
                        up_rxn = True
                except IndexError:
                    pass
                try:
                    if party[cur_index-1] and cur_index != 0:
                        await msg.add_reaction(ARROWS[3])
                        down_rxn = True
                except IndexError:
                    pass
            else:
                await msg.add_reaction('\N{WHITE HEAVY CHECK MARK}')
            await msg.add_reaction('\N{BLACK SQUARE FOR STOP}')

            try:
                rxn, user = await self.bot.wait_for('reaction_add', timeout=115,
                                                    check=lambda r, u: u.id == ctx.author.id and r.message.id == msg.id)
            except asyncio.TimeoutError:
                break

            party_pokemon = await ctx.con.fetch("""
                                          SELECT * FROM found WHERE party_position IS NOT NULL and owner=$1
                                          """, ctx.author.id)
            if str(rxn) == '\N{PENCIL}':
                em = discord.Embed()
                em.color = embed.color
                em.description = 'Enter a nickname for your Pokemon'
                em.set_image(url='attachment://pokemon.gif')
                await msg.edit(embed=em, delete_after=120)

                try:
                    response = await self.bot.wait_for('message', timeout=115, check=lambda m: m.author.id == ctx.author.id)
                except asyncio.TimeoutError:
                    break
                mon_name = await ctx.con.fetchval("""
                                         SELECT name FROM pokemon WHERE num=$1
                                         """, chosen_mon['num'])

                if response.content.lower() == mon_name.lower():
                    await ctx.con.execute("""
                                  UPDATE found SET name=$1 WHERE id=$2
                                  """, None, chosen_mon['id'])
                else:
                    await ctx.con.execute("""
                                  UPDATE found SET name=$1 WHERE id=$2
                                  """, response.content, chosen_mon['id'])

                await response.delete()
                break
            elif str(rxn) == '\N{WHITE HEAVY CHECK MARK}':
                if len(party_pokemon) >= self.max_party_size:
                    await ctx.send('Your party is full!')
                    break
                else:
                    for i in range(self.max_party_size):
                        if i not in [p['party_position'] for p in party_pokemon]:
                            await ctx.con.execute("""
                                          UPDATE found SET party_position=$1 WHERE id=$2
                                          """, i, chosen_mon['id'])
                            break
            elif str(rxn) == '\N{CROSS MARK}':
                if chosen_mon['party_position'] == None:
                    await ctx.send('This Pokemon is not in your party!')
                    break
                else:
                    position = chosen_mon['party_position']
                    await ctx.con.execute("""
                                  UPDATE found SET party_position=NULL WHERE id=$1
                                  """, chosen_mon['id'])
                    await ctx.con.execute("""
                                  UPDATE found SET party_position=party_position-1 WHERE party_position>$1 AND owner=$2
                                  """, position, ctx.author.id)
            elif str(rxn) == ARROWS[2]:
                pos = chosen_mon['party_position']
                if not up_rxn:
                    pass
                else:
                    await ctx.con.execute("""
                                  UPDATE found SET party_position=$1 WHERE party_position=$2 AND owner=$3
                                  """, pos, pos+1, ctx.author.id)
                    await ctx.con.execute("""
                                  UPDATE found SET party_position=$1 WHERE id=$2
                                  """, pos+1, chosen_mon['id'])
            elif str(rxn) == ARROWS[3]:
                pos = chosen_mon['party_position']
                if not down_rxn:
                    pass
                else:
                    await ctx.con.execute("""
                                  UPDATE found SET party_position=$1 WHERE party_position=$2 AND owner=$3
                                  """, pos, pos-1, ctx.author.id)
                    await ctx.con.execute("""
                                  UPDATE found SET party_position=$1 WHERE id=$2
                                  """, pos-1, chosen_mon['id'])
            elif evo_dict and rxn.emoji in [evo_dict[e][1] for e in evo_dict]:
                name = process.extractOne(rxn.emoji.name, evo_dict.keys())[0]
                evolved = await check_evolve(ctx, chosen_mon)
                if evolved is not None:
                    await ctx.con.execute("""
                                  UPDATE found SET num=$1 WHERE id=$2
                                  """, evolved, chosen_mon['id'])
                inv = trainer['inventory']
                inv[name] -= 1
                await stats_logger.log_event(ctx, 'item_used', item=name)
                if inv[name] == 0:
                    del inv[name]
                await set_inventory(ctx, ctx.author.id, inv)
                break
            else:
                break

            try:
                await msg.clear_reactions()
            except:
                pass

        try:
            await msg.delete()
        except:
            pass

###################
#                 #
# Party           #
#                 #
###################

    @checks.db
    @commands.command()
    @pokechannel()
    async def party(self, ctx):
        party = await ctx.con.fetch("""
                              SELECT p.*, f.*, f.name as name, p.name as base_name
                              FROM pokemon AS p JOIN found as f ON p.num = f.num AND p.form_id = f.form_id
                              WHERE f.party_position IS NOT NULL AND f.owner=$1 ORDER BY f.party_position ASC
                              """, ctx.author.id)
        await stats_logger.log_event(ctx, 'party_accessed')
        header = f"__**{ctx.author.name}'s Party**__"
        if len(party) == 0:
            header += " __**is empty.**__"
            return await ctx.send(header, delete_after=60)
        spacer = SPACER * 24

        key = f'{CANCEL} Click to exit your party.'

        header = '\n'.join([header, spacer, 'Use **!pc info ``name``** to see statistics for a specific Pokémon.',
                            spacer, key])

        party_display = '\n'.join([f"{i+1}. {get_name(mon)}{get_star(mon)}" for (i, mon) in enumerate(party)])
        out = f"{header}\n\n{party_display}"
        msg = await ctx.send(out)

        await msg.add_reaction('\u267b')
        await msg.add_reaction('\N{CROSS MARK}')

        try:
            rxn, usr = await self.bot.wait_for('reaction_add', timeout=115,
                                               check=lambda r, u: r.message.id == msg.id and u.id == ctx.author.id)
        except asyncio.TimeoutError:
            await msg.delete()
            return

        if str(rxn) == '\u267b':
            await ctx.con.execute("""
                          UPDATE found SET party_position=NULL WHERE party_position IS NOT NULL and owner=$1
                          """, ctx.author.id)

        await msg.delete()


###################
#                 #
# POKEDEX         #
#                 #
###################

    async def get_pokedex_embed(self, ctx, mon, shiny=False):
        pokedex = self.bot.get_emoji_named('Pokedex')
        evo = await get_evolution_chain(ctx, mon['num'])
        embed = discord.Embed(description=wrap(f"__{mon['name']}{get_star(mon)}'s Information__", pokedex) +
                              f"\n**ID:** {mon['num']}\n**Type:** {' & '.join(mon['type'])}"
                              f"\n**Evolutions:**\n{evo}")
        embed.color = await get_pokemon_color(ctx, mon=mon)
        embed.set_image(url='attachment://pokemon.gif')

        return embed

    @checks.db
    @commands.group(invoke_without_command=True)
    @pokechannel()
    async def pokedex(self, ctx, *, member=None):
        """Shows you your Pokedex through a reaction menu."""
        await stats_logger.log_event(ctx, 'pokedex_accessed', query=member, shiny=False)
        pokedex = self.bot.get_emoji_named('Pokedex')

        member = await poke_converter(ctx, member) or ctx.author

        total_pokemon = await ctx.con.fetchval("""
                                      SELECT COUNT(DISTINCT num) FROM pokemon
                                      """)
        if isinstance(member, discord.Member):
            seen = await ctx.con.fetch("""
                                 WITH p AS (SELECT num, name, mythical, legendary FROM pokemon WHERE form_id = 0)
                                 SELECT s.num, name, mythical, legendary FROM seen s JOIN p ON s.num = p.num
                                 WHERE user_id=$1 ORDER BY s.num
                                 """, member.id)
            total_found = len(seen)

            legendaries = sum(1 for m in seen if m['legendary'] and not m['mythical'])
            mythicals = sum(1 for m in seen if m['mythical'])

            header = f"__**{member.name}'s Pokedex**__"
            if total_found == 0:
                header += " __**is empty.**__"
            header = wrap(header, pokedex)
            if total_found == 0:
                return await ctx.send(header, delete_after=60)

            spacer = SPACER * 22

            key = f'{ARROWS[0]} Click to go back a page.\n{ARROWS[1]} Click to go forward a page.\n{CANCEL}' \
                  f' Click to exit your pokedex.'

            counts = wrap(f'**{total_found}** encountered out of {total_pokemon} total Pokemon.'
                          f'\n**{total_found - mythicals - legendaries}** Normal | **{legendaries}** Legendary {STAR}'
                          f' | **{mythicals}** Mythical {GLOWING_STAR}', spacer, sep='\n')
            header = '\n'.join([header, 'Use **!pc** to see which Pokémon you own!\nUse **!pokedex** ``#`` to take a closer look at a Pokémon!', key, counts])

            options = []
            for mon in seen:
                options.append("**{}.** {}{}".format(
                    mon['num'], mon['name'], get_star(mon)))
            await self.reaction_menu(options, ctx.author, ctx.channel, 0, per_page=20, code=False, header=header)
            return
        elif isinstance(member, int):
            if 0 >= member or member > total_pokemon:
                return await ctx.send(f'Pokemon {member} does not exist.')

            image = self.image_path.format('normal', member, 0)
            info = await get_pokemon(ctx, member)
        elif isinstance(member, str):
            pokemon_records = await ctx.con.fetch("""
                                          SELECT name FROM pokemon
                                          """)
            pokemon_names = [mon['name'] for mon in pokemon_records]
            result = list(process.extractOne(member, pokemon_names))
            if result[1] < 70:
                return await ctx.send(f'Pokemon {member} does not exist.')
            pokemon_number = await ctx.con.fetchval("""
                                           SELECT num FROM pokemon WHERE name=$1
                                           """, result[0])
            info = await get_pokemon(ctx, pokemon_number)
            image = self.image_path.format('normal', info['num'], 0)
        embed = await self.get_pokedex_embed(ctx, info)
        await ctx.send(embed=embed, file=discord.File(image, filename='pokemon.gif'), delete_after=120)

    @checks.db
    @pokedex.command(name='shiny')
    @pokechannel()
    async def pokedex_shiny(self, ctx, *, pokemon):
        try:
            pokemon = int(pokemon)
        except ValueError:
            pass

        await stats_logger.log_event(ctx, 'pokedex_accessed', query=pokemon, shiny=True)
        total_pokemon = await ctx.con.fetchval("""
                                      SELECT COUNT(DISTINCT num) FROM pokemon
                                      """)
        if isinstance(pokemon, int):
            if 0 >= pokemon or pokemon > total_pokemon:
                return await ctx.send(f'Pokemon {pokemon} does not exist.')

            image = self.image_path.format('shiny', pokemon, 0)
            info = await get_pokemon(ctx, pokemon)
        elif isinstance(pokemon, str):
            pokemon_records = await ctx.con.fetch("""
                                          SELECT name FROM pokemon
                                          """)
            pokemon_names = [mon['name'] for mon in pokemon_records]
            result = list(process.extractOne(pokemon, pokemon_names))
            if result[1] < 70:
                return await ctx.send(f'Pokemon {pokemon} does not exist.')

            pokemon_number = await ctx.con.fetchval("""
                                           SELECT num FROM pokemon WHERE name=$1
                                           """, result[0])
            info = await get_pokemon(ctx, pokemon_number)
            image = self.image_path.format('shiny', info['num'], 0)
        embed = await self.get_pokedex_embed(ctx, info, shiny=True)
        await ctx.send(embed=embed, file=discord.File(image, filename='pokemon.gif'), delete_after=120)

###################
#                 #
# TRADE           #
#                 #
###################

    @checks.db
    @commands.command()
    @pokechannel()
    async def trade(self, ctx, *, user: discord.Member):
        """Trade pokemon with another user."""
        author = ctx.author
        if author.id == user.id:
            await ctx.send('You cannot trade with yourself.', delete_after=60)
            return
        channel = ctx.channel
        cancelled = '**{}** cancelled the trade.'
        get_found = await ctx.con.prepare("""
                                  WITH p AS (SELECT num, name, form, form_id, legendary, mythical FROM pokemon)
                                  SELECT f.id, f.num, f.name, exp, item, original_owner, personality,
                                         p.name AS base_name, p.form, legendary, mythical FROM found f
                                  JOIN p ON p.num = f.num AND p.form_id = f.form_id
                                  WHERE owner = $1 ORDER BY f.num, f.form_id;
                                  """)
        a_found = [dict(m) for m in await get_found.fetch(author.id)]
        b_found = [dict(m) for m in await get_found.fetch(user.id)]
        trainer_ids = set(m['original_owner'] for m in itertools.chain(a_found, b_found))
        trainers = {t['user_id']: t for t in await ctx.con.fetch("""
                                                           SELECT * FROM trainers WHERE user_id = ANY($1)
                                                           """, trainer_ids)}

        a_names = []
        a_options = []
        for mon in a_found:
            mon['fname'] = get_name(mon)
            mon['shiny'] = is_shiny(trainers[mon['original_owner']], mon['personality'])
            a_names.append(mon['fname'] + mon['shiny'])
            a_options.append("**{}.** {}{}{}".format(
                mon['num'], mon['fname'], get_star(mon), mon['shiny']))

        b_names = []
        b_options = []
        for mon in b_found:
            mon['fname'] = get_name(mon)
            mon['shiny'] = is_shiny(trainers[mon['original_owner']], mon['personality'])
            b_names.append(mon['fname'] + mon['shiny'])
            b_options.append("**{}.** {}{}{}".format(
                mon['num'], mon['fname'], get_star(mon), mon['shiny']))

        header = '**{.name}**,\nSelect the pokemon you wish to trade with **{.name}**'
        selected = await asyncio.gather(self.reaction_menu(a_options, author, channel, -1, code=False,
                                                           header=header.format(author, user), return_from=a_found,
                                                           allow_none=True, multi=True, display=a_names),

                                        self.reaction_menu(b_options, user, channel, -1, code=False,
                                                           header=header.format(user, author), return_from=b_found,
                                                           allow_none=True, multi=True, display=b_names))
        if all(s is None for s in selected):
            await ctx.send('No one responded to the trade.', delete_after=60)
            return
        elif selected[0] is None:
            await ctx.send(cancelled.format(author), delete_after=60)
            return
        elif selected[1] is None:
            await ctx.send(cancelled.format(user), delete_after=60)
            return
        for selections, found, member in zip(selected, (a_found, b_found), (author, user)):
            added_ids = []
            for mon in selections:
                if mon['id'] in added_ids:
                    await ctx.send(f'{member.name} selected more {get_name(mon)} than they have.',
                                   delete_after=60)
                    return
                added_ids.append(mon['id'])
        accept_msg = await ctx.send("**{}**'s offer: {}\n**{}**'s offer: {}\nDo you accept?".format(
            author.name, '**,** '.join(mon['fname'] for mon in selected[0]) or 'None',
            user.name, '**,** '.join(mon['fname'] for mon in selected[1]) or 'None'))
        await accept_msg.add_reaction(DONE)
        await accept_msg.add_reaction(CANCEL)
        accepted = {author.id: None, user.id: None}
        accept_reaction = None
        reacted = None

        def accept_check(reaction, reaction_user):
            if reaction.message.id != accept_msg.id or reaction.emoji not in (DONE, CANCEL):
                return False
            if reaction.emoji == DONE:
                nonlocal accept_reaction
                accept_reaction = reaction
            if reaction_user.id in accepted:
                accept = reaction.emoji == DONE
                accepted[reaction_user.id] = accept
                if not accept:
                    return True
            return all(isinstance(value, bool) for value in accepted.values())

        try:
            with aiohttp.Timeout(60):
                while True:
                    await self.bot.wait_for('reaction_add', check=accept_check)
                    if accepted[author.id] and accepted[user.id]:
                        reacted = await accept_reaction.users().flatten()
                        if author in reacted and user in reacted:
                            break
                    elif any(not value for value in accepted.values()):
                        break
        except asyncio.TimeoutError:
            pass

        if all(accepted[u.id] is None for u in (author, user)):
            await ctx.send('No one responded to the trade.', delete_after=60)
            await accept_msg.delete()
            return

        for u in (author, user):
            if reacted and u not in reacted:
                accepted[u.id] = False
            if not accepted[u.id]:
                await ctx.send(f'**{u.name}** declined the trade.', delete_after=60)
                await accept_msg.delete()
                return
        await get_player(ctx, ctx.author.id)
        await get_player(ctx, user.id)
        for selection, old, new in zip(selected, (author, user), (user, author)):
            await see(ctx, new.id, (m['num'] for m in selection))
            for mon in selection:
                evolved = await check_evolve(ctx, mon, trade_for=selected[not selected.index(selection)], trading=True)
                if evolved:
                    await ctx.con.execute("""
                                  UPDATE found SET num=$1 WHERE id=$2
                                  """, evolved, mon['id'])

            await ctx.con.execute("""
                          UPDATE found SET owner=$1, party_position=NULL WHERE id=ANY($2) AND owner=$3
                          """, new.id, [mon['id'] for mon in selection], old.id)
        offers = [[mon['id'] for mon in selected] for selected in selection]
        await stats_logger.log_event(ctx, 'successful_trade', other_id=user.id, offer=offers[0], other_offer=offers[1])
        await accept_msg.delete()
        await ctx.send(f'Completed trade between **{author.name}** and **{user.name}**.', delete_after=60)


def setup(bot):
    bot.add_cog(Pokemon(bot))

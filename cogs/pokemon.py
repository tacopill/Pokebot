from random import randint
import itertools
import asyncio
import random
import re

import aiohttp
import discord
from discord.ext import commands
from fuzzywuzzy import process

from utils import errors
from utils.orm import *
from utils.menus import Menus, STAR, GLOWING_STAR, SPARKLES, SPACER, ARROWS, DONE, CANCEL
from utils.utils import wrap

converter = commands.MemberConverter()


pokeballs = ('Pokeball', 'Greatball', 'Ultraball', 'Masterball')


def pokechannel():
    def check(ctx):
        if ctx.guild is None:
            return True
        if ctx.channel.name in ['pokemon']:
            return True
        raise errors.WrongChannel(discord.utils.get(ctx.guild.channels, name='pokemon'))
    return commands.check(check)


def catch(mon, ball):
    r = randint(1, 100)
    legendary = mon.legendary
    mythical = mon.mythical
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


class PokemonGame(Menus):
    def __init__(self, bot):
        self.bot = bot
        self.image_path = 'data/pokemon/images/{}/{}-{}.gif'
        self.max_party_size = 4


###################
#                 #
# POKEMON         #
#                 #
###################

    @commands.group(invoke_without_command=True, aliases=['pokemen', 'pokermon', 'digimon'])
    @commands.cooldown(1, 150, commands.BucketType.user)
    @pokechannel()
    async def pokemon(self, ctx):
        """Gives you a random Pokemon!"""
        player_name = ctx.author.name
        player_id = ctx.author.id
        trainer = await Trainer.from_user_id(ctx, player_id)
        mon = await Pokemon.random(ctx, trainer)
        shiny = GLOWING_STAR if mon.shiny else ''
        await ctx.log_event('pokemon_encountered', num=mon.num, shiny=mon.shiny)

        inv = trainer.inventory
        balls = [self.bot.get_emoji_named(ball) for ball in pokeballs if inv.get(ball)]
        embed = discord.Embed(description=f'A wild **{mon.display_name}**{mon.star}{shiny} appears!' +
                              (f'\nUse a {balls[0]} to catch it!' if balls else ''))
        embed.color = mon.color
        embed.set_author(icon_url=ctx.author.avatar_url, name=player_name)
        embed.set_image(url='attachment://pokemon.gif')
        msg = await ctx.send(embed=embed, file=discord.File(self.image_path.format('normal', mon.num, 0),
                                                            filename='pokemon.gif'))
        await trainer.see(mon)
        catch_attempts = 0
        while catch_attempts <= 2:
            trainer = await Trainer.from_user_id(ctx, player_id)
            inv = trainer.inventory
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

                await ctx.log_event('item_used', item=reaction.emoji.name)
            except asyncio.TimeoutError:
                embed.description = f'**{mon.display_name}**{mon.star}{shiny} escaped because you took too long!' \
                                    f' :stopwatch:'
                await msg.edit(embed=embed, delete_after=60)
                await msg.clear_reactions()
                return
            await msg.clear_reactions()
            if reaction.emoji in balls:
                inv[reaction.emoji.name] -= 1
                await trainer.set_inventory(inv)
                catch_attempts += 1
                if catch(mon, balls.index(reaction.emoji)):
                    embed.description = wrap(f'You caught **{mon.display_name}**{mon.star}{shiny} successfully!',
                                             reaction.emoji)
                    await msg.edit(embed=embed, delete_after=60)
                    found = await trainer.add_caught_pokemon(mon, reaction.emoji.name)
                    await ctx.log_event('pokemon_caught', attempts=catch_attempts+1, ball=reaction.emoji.name,
                                        id=found.id)
                    break
                else:
                    escape_quotes = ['Oh no! The Pokémon broke free!', 'Aww... It appeared to be caught!',
                                     'Aargh! Almost had it!', 'Gah! It was so close, too!']
                    embed.description = random.choice(escape_quotes)
                await msg.edit(embed=embed)
            else:
                embed.description = wrap(f'You ran away from **{mon.display_name}**{mon.star}{shiny}!', ':chicken:')
                await msg.edit(embed=embed, delete_after=60)
                await ctx.log_event('pokemon_fled', attempts=catch_attempts+1, num=mon.num, shiny=mon.shiny)
                break
        else:
            embed.description = f'**{mon.display_name}**{mon.star}{shiny} has escaped!'
            await ctx.log_event('pokemon_fled', attempts=catch_attempts+1, num=mon.num, shiny=mon.shiny)
            await msg.edit(embed=embed, delete_after=60)

###################
#                 #
# PC              #
#                 #
###################

    @commands.group(invoke_without_command=True)
    @pokechannel()
    async def pc(self, ctx, *, member: discord.Member = None):
        """Opens your PC."""
        member = member or ctx.author
        await ctx.log_event('pc_accessed', query_type='member', query=member.id)

        total_pokemon = len(await get_all_pokemon(ctx))
        trainer = await Trainer.from_user_id(ctx, member.id)
        found = await trainer.get_pokemon()
        total_found = len(found)
        remaining = total_pokemon - total_found

        legendaries = len([m for m in found if m.legendary])
        mythics = len([m for m in found if m.mythical])

        header = f"__**{member.name}'s PC**__"
        if total_found == 0:
            header += " __**is empty.**__"
        if total_found == 0:
            return await ctx.send(header, delete_after=60)
        spacer = SPACER * 21

        key = f'{ARROWS[0]} Click to go back a page.\n{ARROWS[1]} Click to go forward a page.\n{CANCEL}' \
              f' Click to exit your pc.'

        counts = wrap(f'**{total_found}** collected out of {total_pokemon} total Pokemon. {remaining} left to go!'
                      f'\n**{total_found - mythics - legendaries}** normal | **{legendaries}** Legendary {STAR}'
                      f' | **{mythics}** Mythical {GLOWING_STAR}', spacer, sep='\n')

        header = '\n'.join([header, 'Use **!pokedex** to see which Pokémon you\'ve encountered!\nUse **!pokedex** ``#``'
                                    ' to take a closer look at a Pokémon!', key, counts])

        options = []
        done = []
        for mon in found:
            if mon.display_name in done and mon.party_position is None:
                continue
            if mon.party_position is None:
                mon_count = sum(m.display_name == mon.display_name for m in found if m.party_position is None)
                done.append(mon.display_name)
            elif mon.party_position is not None:
                mon_count = 1
            shiny = mon.shiny
            shiny = SPARKLES if shiny else ''
            count = f" x{mon_count}" if mon_count > 1 else ''
            name = mon.display_name
            options.append("{} **{}.** {}{}{}{}".format('' if mon.party_position is not None else '',
                                                        mon.num, name, mon.star, shiny, count))
        await self.reaction_menu(options, ctx.author, ctx.channel, 0, per_page=20, code=False, header=header)

    async def get_pc_info_embed(self, mon):
        pokedex = self.bot.get_emoji_named('Pokedex')
        em = discord.Embed()
        em.color = mon.color
        name = mon.display_name
        level = mon.level
        needed_xp = xp_to_level(level+1) - xp_to_level(level)
        current_xp = mon.exp - xp_to_level(level)
        bar_length = 10
        FILLED_BAR = '■'
        UNFILLED_BAR = '□'
        bar = f'[{UNFILLED_BAR * bar_length}]()'
        percent_needed = (current_xp / needed_xp)
        filled_bars = int(bar_length * percent_needed)
        if filled_bars != 0:
            bar = f"[{(FILLED_BAR * filled_bars).ljust(bar_length, UNFILLED_BAR)}]()"

        em.description = wrap(f'__Your {name}{mon.star}\'s Information__', pokedex)
        em.description += f"\n**ID:** {mon.num}\n" \
                          f"**Level:** {level}\n" \
                          f"**EXP:** {current_xp}/{needed_xp}\n{bar}\n" \
                          f"**Type:** {' & '.join(mon.type)}\n" \
                          f"**Caught Using:** {self.bot.get_emoji_named(mon.ball)}\n"

        if mon.party_position is not None:
            em.description += f"**Party Position**: {mon.party_position + 1}"

        shiny_status = 'shiny' if mon.shiny else 'normal'
        image = self.image_path.format(shiny_status, mon.num, 0) # replace 0 with mon['form_id'] to support forms

        stats = mon.stats
        em.add_field(name='Statistics', value='\n'.join(f"**{stat.replace('_', '. ').title()}**: {val}"
                                                        for stat, val in stats.items()))
        evo = await mon.get_evolution_chain()
        em.add_field(name='Evolutions', value=evo, inline=False)
        return em, image

    @pc.command(name='info')
    @pokechannel()
    async def pc_info(self, ctx, *, query: str):
        """Display information for a specific Pokemon from the user's PC."""
        trainer = await Trainer.from_user_id(ctx, ctx.author.id)
        if query.isdigit():  # If the user enters a Pokemon's number.
            query_type = 'num'
            query = int(query)
            mon_list = await FoundPokemon.from_num(ctx, query)
        elif any(comparator in query for comparator in ['<', '>', '=']):  # Advanced query
            valid_stats = ['hp', 'attack', 'defense', 'sp_attack', 'sp_defense', 'speed']
            query_type = 'statistic'

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
            found = await trainer.get_pokemon()
            mon_list = []
            for record in found:
                stats = record.stats
                if eval(f"""{stats[stat]} {comparator} {value}"""):
                    mon_list.append(record)
            if not mon_list:
                return await ctx.send(f'Pokemon with `{result}` does not exist.', delete_after=60)
        else:  # Fuzzy match the query with all the Pokemon in the user's PC.
            query_type = 'fuzzy'
            pokemon_names = [p.base_name for p in await get_all_pokemon(ctx)]
            result = process.extractOne(query, pokemon_names)
            if result[1] < 70:
                return await ctx.send(f'Pokemon {query} does not exist.', delete_after=60)
            pokemon = await ctx.con.fetch("""
                SELECT * FROM found WHERE owner=$1 AND num=ANY(SELECT num FROM pokemon WHERE base_name=$2) ORDER BY party_position, num, form_id
                """, ctx.author.id, result[0])
            mon_list = [await FoundPokemon.from_id(ctx, p['id']) for p in pokemon]

        await ctx.log_event('pc_accessed', query=query, query_type=query_type)

        if len(mon_list) == 1:
            embed, im = await self.get_pc_info_embed(mon_list[0])
            embed.set_image(url='attachment://pokemon.gif')
            chosen_mon = mon_list[0]
        else:
            mon_names = []
            for mon in mon_list:
                mon_names.append("{} **{}.** {}{}".format('\📍' if mon.party_position is not None else '',
                                                          mon.num, mon.display_name, mon.star))

            header = f"__**{ctx.author.name}'s PC**__"
            selected = await self.reaction_menu(mon_names, ctx.author, ctx.channel, 1, per_page=10, code=False,
                                                header=header, return_from=mon_list)
            if not selected:
                return

            embed, im = await self.get_pc_info_embed(selected[0])
            embed.set_image(url='attachment://pokemon.gif')
            chosen_mon = selected[0]

        msg = await ctx.send(embed=embed, file=discord.File(im, filename='pokemon.gif'), delete_after=120)
        while True:
            chosen_mon = await FoundPokemon.from_id(ctx, chosen_mon.id)
            party = await trainer.get_pokemon(party=True)
            em, im = await self.get_pc_info_embed(chosen_mon)
            em.set_image(url='attachment://pokemon.gif')
            await msg.edit(embed=em, delete_after=120)
            await msg.add_reaction('\N{PENCIL}')
            up_rxn = None
            down_rxn = None

            evo_info = chosen_mon.evolution_info
            evo_dict = {info['item']: [info['next'], self.bot.get_emoji_named(info['item'])] for info in evo_info
                        if info['item'] is not None}
            trainer = await Trainer.from_user_id(ctx, ctx.author.id)
            for key, val in evo_dict.items():
                if key in trainer.inventory:
                    await msg.add_reaction(val[1])

            if chosen_mon.party_position is not None:
                await msg.add_reaction('\N{CROSS MARK}')
                cur_index = [p.id for p in party].index(chosen_mon.id)
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

            party_pokemon = await trainer.get_pokemon(party=True)
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
                await chosen_mon.set_name(response.content)

                await response.delete()
                break
            elif str(rxn) == '\N{WHITE HEAVY CHECK MARK}':
                if len(party_pokemon) >= self.max_party_size:
                    await ctx.send('Your party is full!')
                    break
                else:
                    for i in range(self.max_party_size):
                        if i not in [p.party_position for p in party_pokemon]:
                            await chosen_mon.set_party_position(i)
                            break
            elif str(rxn) == '\N{CROSS MARK}':
                if chosen_mon.party_position is None:
                    await ctx.send('This Pokemon is not in your party!')
                    break
                else:
                    position = chosen_mon.party_position
                    await chosen_mon.set_party_position(None)
                    await ctx.con.execute("""
                        UPDATE found SET party_position=party_position-1 WHERE party_position>$1 AND owner=$2
                        """, position, ctx.author.id)
            elif str(rxn) == ARROWS[2]:
                pos = chosen_mon.party_position
                if not up_rxn:
                    pass
                else:
                    await ctx.con.execute("""
                        UPDATE found SET party_position=$1 WHERE party_position=$2 AND owner=$3
                        """, pos, pos+1, ctx.author.id)
                    await chosen_mon.set_party_position(pos+1)
            elif str(rxn) == ARROWS[3]:
                pos = chosen_mon.party_position
                if not down_rxn:
                    pass
                else:
                    await ctx.con.execute("""
                        UPDATE found SET party_position=$1 WHERE party_position=$2 AND owner=$3
                        """, pos, pos-1, ctx.author.id)
                    await chosen_mon.set_party_position(pos - 1)
            elif evo_dict and rxn.emoji in [evo_dict[e][1] for e in evo_dict]:
                name = process.extractOne(rxn.emoji.name, evo_dict.keys())[0]
                evolved = await chosen_mon.check_evolve()
                if evolved is not None:
                    await chosen_mon.evolve(evolved)
                inv = trainer.inventory
                inv[name] -= 1
                await ctx.log_event('item_used', item=name)
                if inv[name] == 0:
                    del inv[name]
                await trainer.set_inventory(inv)
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

    @commands.command()
    @pokechannel()
    async def party(self, ctx):
        trainer = await Trainer.from_user_id(ctx, ctx.author.id)
        party = await trainer.get_pokemon(party=True)
        await ctx.log_event('party_accessed')
        header = f"__**{ctx.author.name}'s Party**__"
        if len(party) == 0:
            header += " __**is empty.**__"
            return await ctx.send(header, delete_after=60)
        spacer = SPACER * 24

        key = f'{CANCEL} Click to exit your party.'

        header = '\n'.join([header, spacer, 'Use **!pc info ``name``** to see statistics for a specific Pokémon.',
                            spacer, key])

        party_display = '\n'.join([f"{i+1}. {mon.display_name}{mon.star}" for (i, mon) in enumerate(party)])
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

    async def get_pokedex_embed(self, mon):
        pokedex = self.bot.get_emoji_named('Pokedex')
        evo = await mon.get_evolution_chain()
        embed = discord.Embed(description=wrap(f"__{mon.display_name}{mon.star}'s Information__", pokedex) +
                              f"\n**ID:** {mon.num}\n**Type:** {' & '.join(mon.type)}"
                              f"\n**Evolutions:**\n{evo}")
        embed.color = mon.color
        embed.set_image(url='attachment://pokemon.gif')

        return embed

    @commands.group(invoke_without_command=True)
    @pokechannel()
    async def pokedex(self, ctx, *, member=None):
        """Shows you your Pokedex through a reaction menu."""
        pokedex = self.bot.get_emoji_named('Pokedex')

        member = await poke_converter(ctx, member) or ctx.author

        total_pokemon = len(await get_all_pokemon(ctx))
        if isinstance(member, discord.Member):
            trainer = await Trainer.from_user_id(ctx, member.id)
            await ctx.log_event('pokedex_accessed', query_type='member', query=member.id, shiny=False)
            seen = await trainer.get_pokemon(seen=True)
            total_found = len(seen)

            legendaries = sum(1 for m in seen if m.legendary and not m.mythical)
            mythicals = sum(1 for m in seen if m.mythical)

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
                          f'\n**{total_found - mythicals - legendaries}** normal | **{legendaries}** Legendary {STAR}'
                          f' | **{mythicals}** Mythical {GLOWING_STAR}', spacer, sep='\n')
            header = '\n'.join([header, 'Use **!pc** to see which Pokémon you own!\nUse **!pokedex** ``#`` to take a closer look at a Pokémon!', key, counts])

            options = []
            for mon in seen:
                options.append("**{}.** {}{}".format(
                    mon.num, mon.display_name, mon.star))
            await self.reaction_menu(options, ctx.author, ctx.channel, 0, per_page=20, code=False, header=header)
            return
        elif isinstance(member, int):
            query_type = 'num'
            if 0 >= member or member > total_pokemon:
                return await ctx.send(f'Pokemon {member} does not exist.')

            image = self.image_path.format('normal', member, 0)
            info = await Pokemon.from_num(ctx, member)
        elif isinstance(member, str):
            query_type = 'fuzzy'
            pokemon_names = [p.base_name for p in await get_all_pokemon(ctx)]
            result = list(process.extractOne(member, pokemon_names))
            if result[1] < 70:
                return await ctx.send(f'Pokemon {member} does not exist.')
            pokemon_number = await Pokemon.from_name(ctx, result[0])
            info = await Pokemon.from_num(ctx, pokemon_number.num)
            image = self.image_path.format('normal', info.num, 0)
        else:
            query_type = None
            image = self.image_path.format('normal', 1, 0)
            info = await Pokemon.from_num(ctx, 1)
        embed = await self.get_pokedex_embed(info)
        await ctx.log_event('pokedex_accessed', query_type=query_type, query=member, shiny=False)
        await ctx.send(embed=embed, file=discord.File(image, filename='pokemon.gif'), delete_after=120)

    @pokedex.command(name='shiny')
    @pokechannel()
    async def pokedex_shiny(self, ctx, *, pokemon):
        try:
            pokemon = int(pokemon)
        except ValueError:
            pass

        total_pokemon = len(await get_all_pokemon(ctx))
        if isinstance(pokemon, int):
            query_type = 'num'
            if 0 >= pokemon or pokemon > total_pokemon:
                return await ctx.send(f'Pokemon {member} does not exist.')

            image = self.image_path.format('shiny', pokemon, 0)
            info = await Pokemon.from_num(ctx, pokemon)
        elif isinstance(pokemon, str):
            query_type = 'fuzzy'
            pokemon_names = [p.base_name for p in await get_all_pokemon(ctx)]
            result = list(process.extractOne(pokemon, pokemon_names))
            if result[1] < 70:
                return await ctx.send(f'Pokemon {pokemon} does not exist.')
            pokemon_number = await Pokemon.from_name(ctx, result[0])
            info = await Pokemon.from_num(ctx, pokemon_number.num)
            image = self.image_path.format('shiny', info.num, 0)
        else:
            query_type = None
            image = self.image_path.format('shiny', 1, 0)
            info = await Pokemon.from_num(ctx, 1)
        embed = await self.get_pokedex_embed(info)
        await ctx.log_event('pokedex_accessed', query_type=query_type, query=pokemon, shiny=False)
        await ctx.send(embed=embed, file=discord.File(image, filename='pokemon.gif'), delete_after=120)

###################
#                 #
# TRADE           #
#                 #
###################

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
        trainer1 = await Trainer.from_user_id(ctx, author.id)
        trainer2 = await Trainer.from_user_id(ctx, user.id)
        a_found = await trainer1.get_pokemon()
        b_found = await trainer2.get_pokemon()

        a_names = []
        a_options = []
        for mon in a_found:
            shiny = GLOWING_STAR if mon.shiny else ''
            a_names.append(mon.display_name + shiny)
            a_options.append("**{}.** {}{}{}".format(mon.num, mon.display_name, mon.star, shiny))

        b_names = []
        b_options = []
        for mon in b_found:
            shiny = GLOWING_STAR if mon.shiny else ''
            b_names.append(mon.display_name + shiny)
            b_options.append("**{}.** {}{}{}".format(mon.num, mon.display_name, mon.star, shiny))

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
                if mon.id in added_ids:
                    await ctx.send(f'{member.name} selected more {mon.display_name} than they have.',
                                   delete_after=60)
                    return
                added_ids.append(mon.id)
        accept_msg = await ctx.send("**{}**'s offer: {}\n**{}**'s offer: {}\nDo you accept?".format(
            author.name, '**,** '.join(mon.display_name for mon in selected[0]) or 'None',
            user.name, '**,** '.join(mon.display_name for mon in selected[1]) or 'None'))
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
        for selection, old, new in zip(selected, (author, user), (user, author)):
            new_trainer = await Trainer.from_user_id(ctx, new.id)
            await new_trainer.see(selection)
            for mon in selection:
                evolved = await mon.check_evolve(trade_for=selected[not selected.index(selection)], trading=True)
                if evolved:
                    await mon.evolve(evolved)

                await mon.transfer_ownership(new_trainer)

        offers = [[mon.id for mon in s] for s in selected]
        await accept_msg.delete()
        await ctx.log_event('successful_trade', other_id=user.id, offer=offers[0], other_offer=offers[1])
        await ctx.send(f'Completed trade between **{author.name}** and **{user.name}**.', delete_after=60)


def setup(bot):
    bot.add_cog(PokemonGame(bot))

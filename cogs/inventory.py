from discord.ext import commands
import discord

from cogs.pokemon import pokechannel, get_player, set_inventory, get_name, is_shiny, get_star
from utils.menus import Menus, STAR, GLOWING_STAR, SPACER
import utils.statistics as stats_logger
from utils.utils import wrap, unique
from utils import checks


class Inventory(Menus):
    def __init__(self, bot):
        self.bot = bot

###################
#                 #
# SHOP            #
#                 #
###################

    @checks.db
    @commands.group(invoke_without_command=True)
    @pokechannel()
    async def shop(self, ctx, multiple=1):
        if not multiple:
            return
        await stats_logger.log_event(ctx, 'shop_accessed', multiple=multiple)
        player_name = ctx.author.name
        player_data = await get_player(ctx, ctx.author.id)
        inventory = player_data['inventory']
        thumbnail = 'http://unitedsurvivorsgaming.com/shop.png'
        title = f'{player_name} | {inventory["money"]}\ua750'
        description = 'Select items to buy{}.'.format(f' in multiples of {multiple}' if multiple > 1 else '')
        balls = await ctx.con.fetch("""
                              SELECT name, price FROM items WHERE price != 0 AND name LIKE '%ball' ORDER BY price
                              """)
        balls = [dict(ball) for ball in balls]
        for ball in balls:
            ball['emoji'] = self.bot.get_emoji_named(ball['name'])
        options = ['{} {}\ua750 **|** Inventory: {}'.format(ball['emoji'], ball['price'],
                                                            inventory.get(ball['name'], 0)) for ball in balls]

        selected = await self.embed_menu(options, 'Shop', ctx.author, ctx.channel, -1,
                                         description=description, title=title, thumbnail=thumbnail,
                                         return_from=list(range(len(balls))), multi=True,
                                         display=[ball['emoji'] for ball in balls])
        if not selected:
            return
        bought = []
        total = 0
        for item in set(selected):
            count = selected.count(item) * multiple
            item_info = balls[item]
            item_price, item_name = item_info['price'], item_info['name']
            price = item_price * count
            after = inventory['money'] - price
            if after < 0:
                continue
            total += price
            bought.extend([item] * count)
            inventory['money'] = after
            inventory[item_name] += count
        if total == 0:
            await ctx.send(f"{player_name} didn't buy anything because they're too poor.", delete_after=60)
        else:
            display = []
            for item in set(bought):
                display.append(str(balls[item]['emoji']))
                count = bought.count(item)
                if count > 1:
                    display[-1] += f' x{count}'
            await ctx.send(f'{player_name} bought the following for {total}\ua750:\n' + '\n'.join(display),
                           delete_after=60)
            items = {item: bought.count(item) for item in bought}
            await stats_logger.log_event(ctx, 'shop_purchased', items=items, spent=total)
            await set_inventory(ctx, ctx.author.id, inventory)

###################
#                 #
# SELL            #
#                 #
###################

    @checks.db
    @shop.command()
    @pokechannel()
    async def sell(self, ctx):
        spacer = SPACER * 24
        player_name = ctx.author.name
        user_pokemon = await ctx.con.fetch("""
                                     WITH p AS (SELECT num, name, form, form_id, legendary, mythical FROM pokemon)
                                     SELECT f.id, f.num, f.name, original_owner, personality,
                                            p.name AS base_name, p.form, legendary, mythical FROM found f
                                     JOIN p ON p.num = f.num AND p.form_id = f.form_id
                                     WHERE owner = $1 ORDER BY f.num, f.form_id;
                                     """, ctx.author.id)
        user_pokemon = [dict(mon) for mon in user_pokemon]
        player_data = await get_player(ctx, ctx.author.id)
        await stats_logger.log_event(ctx, 'shop_accessed', multiple=0)
        inventory = player_data['inventory']
        header = f'**{player_name}**,\nSelect Pokemon to sell.\n' + wrap(f'**100**\ua750 Normal | **600**\ua750'
                                                                         f' Legendary {STAR} | **1000**\ua750'
                                                                         f' Mythical {GLOWING_STAR}', spacer, sep='\n')
        names = []
        options = []
        trainers = {t['user_id']: t for t in await ctx.con.fetch("""
                                                           SELECT * FROM trainers WHERE user_id = ANY($1)
                                                           """, set(m['original_owner'] for m in user_pokemon))}
        for mon in user_pokemon:
            name = get_name(mon)
            mon['shiny'] = is_shiny(trainers[mon['original_owner']], mon['personality'])
            options.append("**{}.** {}{}{}".format(
                mon['num'], name, get_star(mon), mon['shiny']))
            names.append(name)
        if not options:
            await ctx.send("You don't have any pokemon to sell.", delete_after=60)
            return
        selected = await self.reaction_menu(options, ctx.author, ctx.channel, -1, per_page=20, header=header,
                                            code=False, multi=True, return_from=user_pokemon, display=names)
        if not selected:
            return
        named = []
        sold = []
        sold_ids = []
        log_list = []
        total = 0
        selected = unique(selected, key=lambda m: m['id'])
        for mon in sorted(selected, key=lambda m: m['num']):
            if mon['shiny']:
                total += 1000
            if mon['mythical']:
                total += 1000
            elif mon['legendary']:
                total += 600
            else:
                total += 100
            sold_ids.append(mon['id'])
            shiny = False
            log_list.append({'id': mon['id']})

            if mon['num'] not in named:
                count = 0
                for m in selected:
                    if m['num'] == mon['num']:
                        count += 1
                        shiny = shiny or m['shiny']
                sold.append(f"{mon['base_name']}{shiny}{f' x{count}' if count > 1 else ''}")
                named.append(mon['num'])
        await ctx.con.execute("""
                    UPDATE found SET owner=NULL WHERE id=ANY($1)
                    """, sold_ids)
        inventory['money'] += total
        await stats_logger.log_event(ctx, 'shop_sold', pokemon=log_list, received=total)
        await set_inventory(ctx, ctx.author.id, inventory)
        await ctx.send(f'{player_name} sold the following for {total}\ua750:\n' + '\n'.join(sold), delete_after=60)

    @checks.db
    @commands.command(aliases=['inv', 'bag'])
    @pokechannel()
    async def inventory(self, ctx):
        thumbnail = 'http://unitedsurvivorsgaming.com/backpack.png'
        await stats_logger.log_event(ctx, 'inventory_accessed')
        player_data = await get_player(ctx, ctx.author.id)
        inv = player_data['inventory']
        all_items = await ctx.con.fetch('''
            SELECT name FROM items ORDER BY id ASC
            ''')
        em = discord.Embed(title=f'{ctx.author.name} | {inv["money"]}\ua750')
        items = []
        for item in all_items[1:]:
            if inv.get(item['name']) or item['name'].endswith('ball'):
                key = self.bot.get_emoji_named(item['name']) or item['name']
                items.append(f"{key} | {inv.get(item['name'])}")
        em.set_thumbnail(url=thumbnail)
        em.add_field(name='Inventory', value='\n'.join(items))
        await ctx.send(embed=em, delete_after=60)

###################
#                 #
# REWARD          #
#                 #
###################

    @checks.db
    @commands.command()
    @commands.cooldown(1, 10800, commands.BucketType.user)
    @pokechannel()
    async def reward(self, ctx):
        """Collect a reward for free every 3 hours!"""
        user = ctx.author
        player_data = await get_player(ctx, user.id)
        inv = player_data['inventory']
        reward = await ctx.con.fetchrow('''
            SELECT * FROM rewards ORDER BY random() LIMIT 1
            ''')
        item, count = reward['name'], reward['num']
        item_name = 'Pokédollar' if item == 'money' else item
        inv[item] = inv.get(item, 0) + count
        await stats_logger.log_event(ctx, 'reward_collected', item=item, amount=count)
        await set_inventory(ctx, user.id, inv)
        await ctx.send(f"{user.name} has received {count} **{item_name}{'s' if count != 1 else ''}**!", delete_after=60)


def setup(bot):
    bot.add_cog(Inventory(bot))
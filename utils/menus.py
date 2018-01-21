from enum import Enum, auto
import asyncio

from discord.ext import commands
import discord

DIGITS = ('\N{DIGIT ONE}\N{COMBINING ENCLOSING KEYCAP}',
          '\N{DIGIT TWO}\N{COMBINING ENCLOSING KEYCAP}',
          '\N{DIGIT THREE}\N{COMBINING ENCLOSING KEYCAP}',
          '\N{DIGIT FOUR}\N{COMBINING ENCLOSING KEYCAP}',
          '\N{DIGIT FIVE}\N{COMBINING ENCLOSING KEYCAP}',
          '\N{DIGIT SIX}\N{COMBINING ENCLOSING KEYCAP}',
          '\N{DIGIT SEVEN}\N{COMBINING ENCLOSING KEYCAP}',
          '\N{DIGIT EIGHT}\N{COMBINING ENCLOSING KEYCAP}',
          '\N{DIGIT NINE}\N{COMBINING ENCLOSING KEYCAP}',
          '\N{KEYCAP TEN}')
ARROWS = ('\N{LEFTWARDS BLACK ARROW}',
          '\N{BLACK RIGHTWARDS ARROW}',
          '\N{UP-POINTING SMALL RED TRIANGLE}',
          '\N{DOWN-POINTING SMALL RED TRIANGLE}')
CANCEL = '\N{CROSS MARK}'
UNDO = '\N{ANTICLOCKWISE DOWNWARDS AND UPWARDS OPEN CIRCLE ARROWS}'  # :arrows_counterclockwise:
DONE = '\N{WHITE HEAVY CHECK MARK}'
SPACER = '\N{BLACK PARALLELOGRAM}'
STAR = '\\\N{WHITE MEDIUM STAR}'
GLOWING_STAR = '\\\N{GLOWING STAR}'
SPARKLES = '\\\N{SPARKLES}'


class MenuControl(Enum):
    previous = auto()
    p = previous
    prev = previous
    last = previous

    next = auto()
    n = next

    done = auto()
    d = done
    finish = done
    f = done

    cancel = auto()
    c = cancel

    undo = auto()
    u = undo


MENU_CONTROLS = (MenuControl.previous, MenuControl.next, MenuControl.cancel, MenuControl.undo, MenuControl.done)


def get_response(content):
    if content.isdigit():
        num = int(content)
        return num
    try:
        return MenuControl[content]
    except KeyError:
        return None


###################
#                 #
#    MENUS        #
#                 #
###################


class Menus:
    async def reaction_prompt(self, message, user, destination, *, timeout=60):
        msg = await destination.send(message)
        for e in (DONE, CANCEL):
            await msg.add_reaction(e)
        try:
            def check(reaction, reaction_user):
                return (reaction.emoji in (DONE, CANCEL) and
                        reaction.message.id == msg.id and
                        reaction_user == user)
            reaction, _ = await self.bot.wait_for('reaction_add', check=check, timeout=timeout)
        except asyncio.TimeoutError:
            return False
        else:
            return reaction.emoji == DONE

###################
#                 #
#    MENU         #
#                 #
###################

    async def menu(self, options, user, destination, count=1, *, timeout=60, multi=False, display=None,
                   code=True, per_page=10, header='', return_from=None, allow_none=False, return_id=False):
        if return_from is None:
            return_from = options
        elif len(return_from) != len(options):
            raise ValueError('return_from length must match that of options')
        if display is None:
            display = options
        elif len(display) != len(options):
            raise ValueError('display length must match that of options')
        if count:
            accept = (*MENU_CONTROLS, *range(1, per_page + 1))
            if count > len(options) and not multi:
                count = len(options)
        else:
            accept = MENU_CONTROLS
        pag = commands.Paginator(prefix='```' if code else '', suffix='```' if code else '')
        page_len = 0
        for ind, line in enumerate(options):
            if page_len == per_page:
                pag.close_page()
                page_len = 0
            if count:
                pag.add_line(f'{ind % 10 + 1}. {line}')
            else:
                pag.add_line(line)
            page_len += 1
        if page_len:
            pag.close_page()
        pages = pag.pages
        if not pages:
            pages = ['None']
            count = 0
        page = 0
        header = header + '\n'
        choices = []
        msg = await destination.send(header + pages[page])

        def check(message):
            response = get_response(message.content)
            return (message.author == user and
                    message.channel == msg.channel and
                    response in accept)

        while True:
            try:
                message = await self.bot.wait_for('message', check=check, timeout=timeout)
                response = get_response(message.content)
            except asyncio.TimeoutError:
                message, response = None, None
            if message is None or response is MenuControl.cancel:
                if response is MenuControl.cancel:
                    await message.delete()
                await msg.delete()
                return user.id if return_id else None
            elif response in accept[5:]:
                if response >= pages[page].count('\n'):
                    continue
                await message.delete()
                choice = page * 10 + response - 1
                if choice not in choices or multi:
                    choices.append(choice)
                    if len(choices) == count:
                        break
            elif response is MenuControl.previous:
                await message.delete()
                page -= 1
            elif response is MenuControl.next:
                await message.delete()
                page += 1
            elif response is MenuControl.undo:
                await message.delete()
                choices.pop()
            elif response is MenuControl.done:
                await message.delete()
                break
            head = header + pages[page]
            if choices:
                head += '\n' + 'Selected: ' + ', '.join(map(str, [display[ind] for ind in choices]))
            await msg.edit(content=head)
        await msg.delete()
        return [return_from[ind] for ind in choices]

###################
#                 #
#    EMBED        #
#    MENU         #
#                 #
###################

    async def embed_menu(self, options, field, user, destination, count=1, *, timeout=60, multi=False, code=True, per_page=10, return_from=None,
                         allow_none=False, return_id=False, display=None, file=None, thumbnail=None, image=None, footer=None, **kwargs):
        if per_page > 20:
            per_page = 20
        if return_from is None:
            return_from = options
        elif len(return_from) != len(options):
            raise ValueError('return_from length must match that of options')
        if display is None:
            display = options
        elif len(display) != len(options):
            raise ValueError('display length must match that of options')
        if count:
            accept = (*MENU_CONTROLS, *range(1, per_page + 1))
            if count > len(options) and not multi:
                count = len(options)
            per_page = 10
        else:
            accept = MENU_CONTROLS
        em = discord.Embed(**kwargs)
        if thumbnail:
            em.set_thumbnail(url=thumbnail)
        if image:
            em.set_image(url=image)
        if footer:
            em.set_footer(text=footer)
        em.add_field(name=field, value='')
        pages = ['']
        page_len = 0
        for ind, line in enumerate(options):
            if page_len == per_page:
                pages.append('')
                page_len = 0
            if count:
                pages[-1] += f'{ind % 10 + 1}. {line}\n'
            else:
                pages[-1] += f'{line}\n'
            page_len += 1
        page = 0
        em._fields[0]['value'] = pages[page]
        choices = []
        msg = await destination.send(embed=em, file=file)

        def check(message):
            response = get_response(message.content)
            return (message.author == user and
                    message.channel == msg.channel and
                    response in accept)

        while True:
            try:
                message = await self.bot.wait_for('message', check=check, timeout=timeout)
                response = get_response(message.content)
            except asyncio.TimeoutError:
                message, response = None, None
            if message is None or response is MenuControl.cancel:
                if response is MenuControl.cancel:
                    await message.delete()
                await msg.delete()
                return user.id if return_id else None
            elif response in accept[5:]:
                if response > pages[page].count('\n'):
                    continue
                await message.delete()
                choice = page * 10 + response - 1
                if choice not in choices or multi:
                    choices.append(choice)
                    if len(choices) == count:
                        break
            elif response is MenuControl.previous:
                await message.delete()
                page -= 1
            elif response is MenuControl.next:
                await message.delete()
                page += 1
            elif response is MenuControl.undo:
                await message.delete()
                choices.pop()
            elif response is MenuControl.done:
                await message.delete()
                break
            if choices:
                em.description = kwargs.get('description', '') + '\n' + 'Selected: ' + ', '.join(map(str, [display[ind] for ind in choices]))
            else:
                em.description = kwargs.get('description')
            em._fields[0]['value'] = pages[page]
            await msg.edit(embed=em)
        await msg.delete()
        return [return_from[ind] for ind in choices]

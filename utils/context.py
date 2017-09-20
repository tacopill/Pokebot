from discord.ext import commands

# Note: stdlib.typing would be used for these, but it breaks isinstance checks.
EVENTS = {
    'pc_accessed': {
        'query': (str, int),  #           str | int
        'query_type': str  # fuzzy, statistic | num, member
    },
    'pokedex_accessed': {
        'query': (str, int),  #            str | int
        'query_type': str,  # fuzzy, statistic | num, member
        'shiny': bool
    },
    'pokemon_encountered': {
        'shiny': bool,
        'num': int
    },
    'pokemon_caught': {
        'attempts': int,
        'ball': str,
        'id': int
    },
    'pokemon_fled': {
        'attempts': int,
        'shiny': bool,
        'num': int
    },
    'party_accessed': {},
    'inventory_accessed': {},
    'item_used': {
        'item': str
    },
    'reward_collected': {
        'amount': int,
        'item': str
    },
    'shop_accessed': {
        'multiple': int
    },
    'shop_purchased': {
        'items': dict,  # typing.Dict[str, int] | {'Item Name': amount}
        'spent': int
    },
    'shop_sold': {
        'pokemon': list,  # typing.List[int] | elements should be `found.id`
        'received': int
    },
    'successful_trade': {
        'other_id': int,
        'offer': list,  # typing.List[int] | elements should be `found.id`
        'other_offer': list  # typing.List[int] | elements should be `found.id`
    }
}


class LogError(ValueError):
    pass


class EventNotFound(LogError):
    pass


class Context(commands.Context):
    async def send(self, *args, **kwargs):
        kwargs['delete_after'] = kwargs.get('delete_after', 60)
        return await super().send(*args, **kwargs)

    async def log_event(self, event, **info):
        if event not in EVENTS:
            raise EventNotFound(event)
        to_insert = {}
        for key, type_ in EVENTS[event].items():
            try:
                value = info[key]
            except KeyError:
                raise LogError(f'"{key}" not given.')
            if not isinstance(value, type_):
                raise LogError(f'"{key}" must be {type_.__name__}, not {type(value).__name__}.')
            to_insert[key] = value

        author_id = self.author.id
        message_id = self.message.id
        channel_id = self.channel.id
        if self.guild:
            guild_id = self.guild.id
        else:
            guild_id = None

        await self.con.execute("""
            INSERT INTO statistics (event_name, user_id, message_id, channel_id, guild_id, information)
            VALUES ($1, $2, $3, $4, $5, $6)
            """, event, author_id, message_id, channel_id, guild_id, to_insert)
        self.bot.dispatch(event, **to_insert)

    async def get_event_count(self, *events):
        if events:
            return await self.con.fetchval("""
                SELECT COUNT(*) FROM statistics WHERE event_name = ANY($1)
                """, events)
        else:
            return await self.con.fetchval("""
                SELECT COUNT(*) FROM statistics
                """)

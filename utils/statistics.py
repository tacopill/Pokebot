async def log_event(ctx, event_name: str, **info):
    user_id = ctx.author.id
    message_id = ctx.message.id
    channel_id = ctx.channel.id

    if ctx.guild:
        guild_id = ctx.guild.id
    else:
        guild_id = None

    await ctx.con.execute("""
                  INSERT INTO statistics (event_name, user_id, message_id, channel_id, guild_id, information) VALUES 
                      ($1, $2, $3, $4, $5, $6)
                      """, event_name, user_id, message_id, channel_id, guild_id, info)


async def get_event_count(ctx, event_name: str):
    count = await ctx.con.fetchval("""
                          SELECT count(*) FROM statistics WHERE event_name=$1
                          """, event_name)
    return count

import asyncio, discord
from discord.ext import commands
from Cogs import Utils, DisplayName, Message

def setup(bot):
    bot.add_cog(Lockdown(bot, bot.get_cog("Settings")))

class Lockdown(commands.Cog):

    # Init with the bot reference, and a reference to the settings var
    def __init__(self, bot, settings):
        self.bot = bot
        self.settings = settings
        self.lockdown_perms = ("send_messages","add_reactions","speak")
        global Utils, DisplayName
        Utils = self.bot.get_cog("Utils")
        DisplayName = self.bot.get_cog("DisplayName")

    def _get_lockdown(self, ctx):
        # Returns a tuple of (lockdown_ids, channel_objects)
        lockdown = self.settings.getServerStat(ctx.guild,"LockdownList",[])
        if not len(lockdown): return ([],None)
        orphaned,channels = self._verify_lockdown(ctx,lockdown)
        if len(orphaned): # Update the lockdown list to reflect changes
            lockdown = [x for x in lockdown if not x in orphaned]
            self.settings.setServerStat(ctx.guild,"LockdownList",lockdown)
        return (lockdown,channels)

    def _verify_lockdown(self, ctx, lockdown):
        # Returns a tuple of (orphaned_ids, channel_objects)
        channels = []
        orphaned = []
        if not isinstance(lockdown,(list,tuple)): lockdown = (lockdown,)
        for c in lockdown:
            channel = ctx.guild.get_channel(int(c))
            if not channel: orphaned.append(c)
            else: channels.append(channel)
        # Let's get the guild by_category() output and sort our channels using that
        ordered = []
        for cat,chan_list in ctx.guild.by_category():
            if cat and not cat.id in ordered: ordered.append(cat.id)
            ordered.extend([chan.id for chan in chan_list])
        # Return the list sorted by discord's GUI position
        return (orphaned,sorted(channels,key=lambda x:ordered.index(x.id)))

    def _get_mention(self,channel):
        # Returns a formatted mention for the passed channel - including
        # the number of synced channels if it's a category
        if isinstance(channel,discord.CategoryChannel):
            synced = [x for x in channel.channels if x.permissions_synced] if hasattr(channel,"permissions_synced") else channel.channels
            return "{} ({:,}/{:,} synced)".format(
                channel.mention,
                len(synced),
                len(channel.channels)
            )
        return "{}{}".format("☑️ " if not hasattr(channel,"permissions_synced") or channel.permissions_synced else "", channel.mention)

    async def _check_lockdown(self, lockdown, ctx):
        # Helper to auto-reply if Lockdown is not configured
        if len(lockdown): return True
        await Message.EmbedText(
            title="Lockdown is not configured!",
            description="You can add channels/categories to the lockdown list with `{}addlock [channel list]`".format(ctx.prefix),
            color=ctx.author
        ).send(ctx)
        return False

    async def _perform_lockdown(self, ctx, target_channels, unlock=False):
        # Helper to lock or unlock channels based on the current context.
        guild = ctx if isinstance(ctx,discord.Guild) else ctx.guild if isinstance(ctx,discord.ext.commands.Context) else None
        if guild is None: return (0,0) # Got sent some wonky values, I guess...
        default_role = guild.default_role
        lockdown = [x for x in target_channels] # Duplicate the list to avoid removing from the original
        categories = channels = 0
        while len(lockdown):
            channel = lockdown.pop(0)
            # Increment counts
            if isinstance(channel,discord.CategoryChannel):
                categories += 1
                # Add all missing channels that do not have synced perms
                new_chans = [x for x in channel.channels if not x in lockdown and (not hasattr(channel,"permissions_synced") or not x.permissions_synced)]
                lockdown.extend(new_chans)
            else:
                channels += 1
                # Check if we even need to update perms based on syncing/category inclusion
                if channel.category and hasattr(channel,"permissions_synced") and channel.permissions_synced and channel.category in target_channels: continue
            overs = channel.overwrites_for(default_role) # Get any overrides for the role
            # Check if we qualify in this channel to (un)lockdown
            if unlock: perm_check  = any(x[0] in self.lockdown_perms and x[1] != None for x in overs)
            else: perm_check = not all([x==False for x in (overs.send_messages,overs.add_reactions,overs.speak)])
            if perm_check: # We qualify - set our perms as needed
                other_perms = any(x[0] not in self.lockdown_perms and x[1] != None for x in overs)
                overs.send_messages = overs.add_reactions = overs.speak = None if unlock else False
                try: await channel.set_permissions(default_role, overwrite=overs if other_perms or not unlock else None)
                except: pass
        return (categories,channels)

    @commands.command()
    async def listlock(self, ctx):
        """Lists the channels and categories configured for lockdown (bot-admin only)."""
        if not await Utils.is_bot_admin_reply(ctx): return
        lockdown,channels = self._get_lockdown(ctx)
        if not await self._check_lockdown(lockdown,ctx): return
        desc = "\n".join([self._get_mention(x) for x in channels])
        await Message.EmbedText(title="Current Lockdown List - {:,} Total".format(len(lockdown)),description=desc,color=ctx.author).send(ctx)

    @commands.command()
    async def checklock(self, ctx):
        """Reports the number of configured channels that are locked down (bot-admin only)."""
        if not await Utils.is_bot_admin_reply(ctx): return
        lockdown,channels = self._get_lockdown(ctx)
        if not await self._check_lockdown(lockdown,ctx): return
        message = await Message.EmbedText(
            title="Checking Lockdown",
            description="Checking {:,} entr{}...".format(len(lockdown),"y" if len(lockdown)==1 else "ies"),
            color=ctx.author
        ).send(ctx)
        default_role = ctx.guild.default_role
        locked = []
        for channel in channels:
            overs = channel.overwrites_for(default_role)
            if all([x==False for x in (overs.send_messages,overs.add_reactions,overs.speak)]):
                locked.append(channel)
        await Message.EmbedText(
            title="Lockdown Status: {}".format("UNLOCKED" if len(locked) == 0 else "LOCKED" if len(locked)==len(lockdown) else "PARTIALLY LOCKED"),
            description="{:,}/{:,} entr{} fully locked down.".format(len(locked),len(lockdown),"y" if len(lockdown)==1 else "ies")
        ).edit(ctx,message)

    @commands.command()
    async def addlock(self, ctx, *, channel_list = None):
        """Adds the passed space-delimited list of channels and categories to the lockdown list (bot-admin only)."""
        if not await Utils.is_bot_admin_reply(ctx): return
        lockdown,channels = self._get_lockdown(ctx)
        if not channel_list: return await ctx.send("Usage: `{}addlock [channel list]`".format(ctx.prefix))
        resolved = []
        resolved_id = []
        for channel in channel_list.split():
            c = DisplayName.channelForName(channel,ctx.guild)
            if c and not c.id in lockdown:
                resolved.append(c)
                resolved_id.append(c.id)
        if not len(resolved): return await ctx.send("No valid channels passed!\nUsage: `{}addlock [channel list]`".format(ctx.prefix))
        lockdown.extend(resolved_id)
        self.settings.setServerStat(ctx.guild,"LockdownList",lockdown)
        desc = "\n".join([self._get_mention(x) for x in resolved])
        await Message.EmbedText(title="{:,} Added to Lockdown List".format(len(resolved)),description=desc,color=ctx.author).send(ctx)

    @commands.command()
    async def addlockall(self, ctx):
        """Adds all channels and categories to the lockdown list (bot-admin only)."""
        if not await Utils.is_bot_admin_reply(ctx): return
        orphaned,channels = self._verify_lockdown(ctx,[x.id for x in ctx.guild.channels])
        if not len(channels): return await ctx.send("No valid channels found!")
        lockdown = [x.id for x in channels]
        self.settings.setServerStat(ctx.guild,"LockdownList",lockdown)
        desc = "\n".join([self._get_mention(x) for x in channels])
        await Message.EmbedText(title="{:,} Added to Lockdown List".format(len(lockdown)),description=desc,color=ctx.author).send(ctx)

    @commands.command()
    async def remlock(self, ctx, *, channel_list = None):
        """Removes the passed space-delimited list of channels and categories from the lockdown list (bot-admin only)."""
        if not await Utils.is_bot_admin_reply(ctx): return
        lockdown,channels = self._get_lockdown(ctx)
        if not await self._check_lockdown(lockdown,ctx): return
        if not channel_list: return await ctx.send("Usage: `{}remlock [channel list]`".format(ctx.prefix))
        resolved = []
        resolved_id = []
        for channel in channel_list.split():
            c = DisplayName.channelForName(channel,ctx.guild)
            if c and c.id in lockdown:
                resolved.append(c)
                resolved_id.append(c.id)
        if not len(resolved): return await ctx.send("No valid channels passed!\nUsage: `{}remlock [channel list]`".format(ctx.prefix))
        lockdown = [x for x in lockdown if not x in resolved_id]
        self.settings.setServerStat(ctx.guild,"LockdownList",lockdown)
        desc = "\n".join([self._get_mention(x) for x in resolved])
        await Message.EmbedText(title="{:,} Removed from Lockdown List".format(len(resolved)),description=desc,color=ctx.author).send(ctx)

    @commands.command()
    async def remlockall(self, ctx):
        """Removes all channels and categories from the lockdown list (bot-admin only)."""
        if not await Utils.is_bot_admin_reply(ctx): return
        lockdown,channels = self._get_lockdown(ctx)
        if not await self._check_lockdown(lockdown,ctx): return
        self.settings.setServerStat(ctx.guild,"LockdownList",[])
        desc = "\n".join([self._get_mention(x) for x in channels])
        await Message.EmbedText(title="{:,} Removed from Lockdown List".format(len(lockdown)),description=desc,color=ctx.author).send(ctx)

    @commands.command()
    async def lockdown(self, ctx, target_channel = None):
        """Iterate through the channels in the lockdown list and revoke the send_message, add_reaction, and speak permissions for the everyone role (bot-admin only)."""
        if not await Utils.is_bot_admin_reply(ctx): return
        if target_channel:
            c = DisplayName.channelForName(target_channel,ctx.guild)
            if not c: return await ctx.send("The passed value is not a valid channel or category!")
            orphaned,channels = self._verify_lockdown(ctx,c.id)
            lockdown = channels
        else:
            lockdown,channels = self._get_lockdown(ctx)
            if not await self._check_lockdown(lockdown,ctx): return
        message = await Message.EmbedText(
            title="Lockdown",
            description="Locking down {:,} entr{}...".format(len(lockdown),"y" if len(lockdown)==1 else "ies"),
            color=ctx.author
        ).send(ctx)
        cats,chans = await self._perform_lockdown(ctx,channels)
        if cats==chans==0: return await Message.EmbedText(title="Lockdown",description="**LOCKDOWN FAILED!**").edit(ctx,message)
        await Message.EmbedText(
            title="Lockdown",
            description="Locked down {}{}.".format(
                "{:,} categor{}".format(cats,"y" if cats==1 else "ies") if cats else "",
                "" if not chans else "{}{:,} channel{}.".format(", " if cats else "",chans,"" if chans==1 else "s")
            )
        ).edit(ctx,message)

    @commands.command()
    async def unlockdown(self, ctx, target_channel = None):
        """Iterate through the channels in the lockdown list and clear the send_message, add_reaction, and speak permissions for the everyone role (bot-admin only)."""
        if not await Utils.is_bot_admin_reply(ctx): return
        if target_channel:
            c = DisplayName.channelForName(target_channel,ctx.guild)
            if not c: return await ctx.send("The passed value is not a valid channel or category!")
            orphaned,channels = self._verify_lockdown(ctx,c.id)
            lockdown = channels
        else:
            lockdown,channels = self._get_lockdown(ctx)
            if not await self._check_lockdown(lockdown,ctx): return
        message = await Message.EmbedText(
            title="Unlockdown",
            description="Unlocking {:,} entr{}...".format(len(lockdown),"y" if len(lockdown)==1 else "ies"),
            color=ctx.author
        ).send(ctx)
        cats,chans = await self._perform_lockdown(ctx,channels,unlock=True)
        if cats==chans==0: return await Message.EmbedText(title="Unlockdown",description="**UNLOCKDOWN FAILED!**").edit(ctx,message)
        await Message.EmbedText(
            title="Unlockdown",
            description="Unlocked {}{}.".format(
                "{:,} categor{}".format(cats,"y" if cats==1 else "ies") if cats else "",
                "" if not chans else "{}{:,} channel{}.".format(", " if cats else "",chans,"" if chans==1 else "s")
            )
        ).edit(ctx,message)

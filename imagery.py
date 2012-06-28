from OpenGL.GLU import *
import PIL.Image

from email.utils import formatdate, parsedate_tz
from math import atan, cos, degrees, exp, floor, log, pi, radians, sin
from os import getenv, makedirs, unlink
from os.path import basename, exists, expanduser, isdir, join
from Queue import Queue
from sys import exit, getfilesystemencoding, platform
from tempfile import gettempdir
import threading
import time
from urllib2 import HTTPError, Request, urlopen
if __debug__:
    from traceback import print_exc
    from prefs import Prefs

from clutter import Draped, DrapedImage, Polygon, tessvertex, tessedge, csgtvertex, csgtcombined, csgtcombine, csgtedge	# no strips
from version import appname

try:
    from Queue import PriorityQueue
except:	# not in Python 2.5
    import heapq
    class PriorityQueue(Queue):
        def _init(self, maxsize):
            self.maxsize = maxsize
            self.queue = []
        def _qsize(self, len=len):
            return len(self.queue)
        def _put(self, item, heappush=heapq.heappush):
            heappush(self.queue, item)
        def _get(self, heappop=heapq.heappop):
            return heappop(self.queue)

fourpi=4*pi

class Filecache:

    directory='dir.txt'
    maxage=31*24*60*60	# Max age in cache since last accessed: 1 month

    def __init__(self):
        if platform.startswith('linux'):
            # http://standards.freedesktop.org/basedir-spec/latest/ar01s03.html
            self.cachedir=(getenv('XDG_CACHE_HOME') or expanduser('~/.cache')).decode(getfilesystemencoding() or 'utf-8')
        elif platform=='darwin':
            self.cachedir=expanduser('~/Library/Caches').decode(getfilesystemencoding() or 'utf-8')
        elif platform=='win32':
            from _winreg import OpenKey, QueryValueEx, HKEY_LOCAL_MACHINE, HKEY_CURRENT_USER, REG_SZ, REG_EXPAND_SZ
            handle=OpenKey(HKEY_CURRENT_USER, 'Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\User Shell Folders')
            (v,t)=QueryValueEx(handle, 'Local AppData')
            handle.Close()
            if t==REG_EXPAND_SZ:
                dirs=v.rstrip('\0').decode('mbcs').strip().split('\\')
                for i in range(len(dirs)):
                    if dirs[i][0]==dirs[i][-1]=='%':
                        dirs[i]=getenv(dirs[i][1:-1],dirs[i]).decode('mbcs')
                v='\\'.join(dirs)
            self.cachedir=v
        try:
            self.cachedir=join(self.cachedir, appname)
            if not isdir(self.cachedir):
                makedirs(self.cachedir)
        except:
            if __debug__: print_exc()
            self.cachedir=join(gettempdir(), appname)
            if not isdir(self.cachedir):
                makedirs(self.cachedir)

        self.files={}
        self.starttime=int(time.time())
        try:
            h=open(join(self.cachedir, Filecache.directory), 'rU')
            for line in h:
                (name, accessed, modified)=line.split()
                if self.starttime-int(accessed) > Filecache.maxage:
                    # Delete files not accessed recently
                    if exists(join(self.cachedir, name)):
                        unlink(join(self.cachedir, name))
                elif exists(join(self.cachedir, name)):
                    self.files[name]=(int(accessed), int(modified))
            h.close()
        except:
            if __debug__: print_exc()


    # Return a file from the cache if present and recent
    def get(self, name):
        now=int(time.time())
        filename=join(self.cachedir, name)
        if name in self.files:
            rec=self.files[name]
            if not rec:
                return False	# Failed to get file
            (accessed, modified)=rec
            if exists(filename) and (accessed>=self.starttime or self.starttime-modified<Filecache.maxage):
                # already accessed this session or downloaded recently - don't check again
                self.files[name]=(now, modified)
                return filename
        return None

    # Return a file from the cache, blocking to read it from a URL if necessary
    def fetch(self, name, url):
        now=int(time.time())
        filename=join(self.cachedir, name)
        if name in self.files:
            rec=self.files[name]
            if not rec:
                return False	# Failed to get file
            (accessed, modified)=rec
            if exists(filename):
                if accessed>=self.starttime or self.starttime-modified<Filecache.maxage:
                    # already accessed this session or downloaded recently - don't check again
                    self.files[name]=(now, modified)
                    return filename
                # http://www.w3.org/Protocols/rfc2616/rfc2616-sec14.html#sec14.25
                # However Bing often ignores this and serves anyway, so first check above whether downloaded recently
                request=Request(url, None, modified and {'If-Modified-Since': formatdate(modified,usegmt=True)} or {})
            else:
                request=Request(url)
        else:
            request=Request(url)

        tries=3
        while tries:
            tries-=1
            try:
                h=urlopen(request)
                d=h.read()
                h.close()
                if h.info().getheader('X-VE-Tile-Info')=='no-tile':
                    # Bing serves a placeholder and adds this header if no imagery available at this resolution
                    raise HTTPError(url, 404, None, None, None)
                f=open(filename, 'wb')
                f.write(d)
                f.close()
                cachecontrol=h.info().getheader('Cache-Control')
                if 'public' in cachecontrol or 'private' in cachecontrol or ('max-age' in cachecontrol and 'no-cache' not in cachecontrol):
                    # http://www.w3.org/Protocols/rfc2616/rfc2616-sec14.html#sec14.9
                    self.files[name]=(now, now)	# just use time of request for modification time
                else:
                    self.files[name]=(now, 0)	# set modificaton time to epoch so will be re-fetched on next run
                if __debug__: self.writedir()
                return filename
            except HTTPError,e:
                if e.code==304:	# Not Modified
                    # http://www.w3.org/Protocols/rfc2616/rfc2616-sec10.html#sec10.3.5
                    self.files[name]=(now, modified)
                    return filename
                elif e.code/100==4:
                    # some kind of unrecoverable error
                    break
                else:
                    # Retry any other errors
                    if __debug__: print_exc()
            except:
                # Retry any other errors
                if __debug__: print_exc()

        # Failed after retries
        self.files[name]=False
        return False


    def writedir(self):
        h=open(join(self.cachedir, Filecache.directory), 'wt')
        for name, rec in sorted(self.files.items()):
            if rec:
                (accessed, modified)=rec
                h.write('%s\t%d\t%d\n' % (name, accessed, modified))
        h.close()
                

class Imagery:

    # Number of simultaneous connections to imagery provider and concurrent placement layouts.
    # Limit to 1 to prevent worker threads slowing UI too much.
    connections=1	

    def __init__(self, canvas):

        self.providers={'Bing': self.bing_setup}

        self.canvas=canvas
        self.imageryprovider=None
        self.provider_url=None
        self.provider_logo=None	# (filename, width, height)
        self.provider_levelmin=self.provider_levelmax=0
        self.placementcache={}	# previously created placements (or None if image couldn't be loaded), indexed by quadkey.
                                # placement may not be laid out if image is still being fetched.
        self.tile=(0,999)	# X-Plane 1x1degree tile - [lat,lon] of SW
        self.loc=None
        self.dist=0

        self.filecache=Filecache()

        # Setup a pool of worker threads
        self.workers=[]
        self.q=PriorityQueue()
        for i in range(Imagery.connections):
            t=threading.Thread(target=self.worker)
            t.daemon=True	# this doesn't appear to work for threads blocked on Queue
            t.start()
            self.workers.append(t)

    # Worker thread
    def worker(self):
        # Each thread gets its own tessellators in thread-local storage
        tls=threading.local()
        tls.tess=gluNewTess()
        gluTessNormal(tls.tess, 0, -1, 0)
        gluTessProperty(tls.tess, GLU_TESS_WINDING_RULE, GLU_TESS_WINDING_NONZERO)
        gluTessCallback(tls.tess, GLU_TESS_VERTEX_DATA,  tessvertex)
        gluTessCallback(tls.tess, GLU_TESS_EDGE_FLAG,    tessedge)
        tls.csgt=gluNewTess()
        gluTessNormal(tls.csgt, 0, -1, 0)
        gluTessProperty(tls.csgt, GLU_TESS_WINDING_RULE, GLU_TESS_WINDING_ABS_GEQ_TWO)
        gluTessCallback(tls.csgt, GLU_TESS_VERTEX_DATA,  csgtvertex)
        if __debug__:
            gluTessCallback(tls.csgt, GLU_TESS_COMBINE,  csgtcombined)
        else:
            gluTessCallback(tls.csgt, GLU_TESS_COMBINE,  csgtcombine)
        gluTessCallback(tls.csgt, GLU_TESS_EDGE_FLAG,    csgtedge)

        while True:
            (priority, fn, args)=self.q.get()
            if not fn: exit()	# Die!
            fn(tls, *args)
            self.q.task_done()


    def exit(self):
        # Closing down
        self.filecache.writedir()
	# kill workers
        for i in range(Imagery.connections):
            self.q.put((0, None, ()))	# Top priority! Don't want to hold up program exit
        # wait for them
        for t in self.workers:
            t.join()


    def reset(self, vertexcache):
        # Called on reload or on new tile. Empty the cache and forget allocations since:
        # a) in the case of reload, textures have been dropped and so would need to be reloaded anyway;
        # b) try to limit cluttering up the VBO with allocations we may not need again;
        # c) images by straddle multiple tiles and these would need to be recalculated anyway.
        for placement in self.placementcache.itervalues():
            if placement: placement.clearlayout(vertexcache)
        self.placementcache={}


    def goto(self, imageryprovider, loc, dist, screensize):

        if not imageryprovider: imageryprovider=None
        if imageryprovider!=self.imageryprovider:
            self.provider_url=None
            self.provider_logo=None
            self.imageryprovider=imageryprovider
            if self.imageryprovider not in self.providers: return
            self.q.put((0, self.providers[self.imageryprovider], ()))

        newtile=(int(floor(loc[0])),int(floor(loc[1])))
        if not self.provider_url or self.tile!=newtile:
            # New tile - drop cache of Clutter
            for placement in self.placementcache.itervalues():
                if placement: placement.clearlayout(self.canvas.vertexcache)
            self.placementcache={}
        self.tile=newtile
        self.loc=loc
        self.placements(dist, screensize)	# Kick off any image loading


    # Return placements to be drawn. May allocate into vertexcache as a side effect.
    def placements(self, dist, screensize):

        level0mpp=2*pi*6378137/256		# metres per pixel at level 0

        if screensize.width<=0 or not (int(self.loc[0]) or int(self.loc[1])) or not self.provider_url:
            return []	# Don't do anything on startup. Can't do anything without a valid provider.

        # layout assumes mesh loaded
        assert (not self.canvas.options&Prefs.ELEVATION) or (int(floor(self.loc[0])),int(floor(self.loc[1])),self.canvas.options&Prefs.ELEVATION) in self.canvas.vertexcache.meshdata, self.canvas.vertexcache.meshdata.keys()

        # http://msdn.microsoft.com/en-us/library/bb259689.aspx
        width=dist+dist				# Width in m of screen (from glOrtho setup)
        ppm=screensize.width/width		# Pixels on screen required by 1 metre, ignoring tilt
        level=min(1+int(log(ppm*level0mpp*cos(radians(self.loc[0])), 2)), self.provider_levelmax)	# zoom level requried
        levelmin=max(13, self.provider_levelmin)	# arbitrary - tessellating out at higher levels takes too long
        level=max(level,levelmin)

        ntiles=2**level				# number of tiles per axis at this level
        #mpp=cos(radians(self.loc[0]))*level0mpp/ntiles		# actual resolution at this level
        #coverage=width/(256*mpp)		# how many tiles to cover screen width - in practice varies between ~2.4 and ~4.8

        (cx,cy)=self.latlon2xy(self.loc[0], self.loc[1], level)	# centre tile
        #print self.loc, width, screensize.width, 1/ppm, ppm, level, ntiles, cx, cy

        # Get 6x6 tiles that cover the same area as 3x3 at the next lower level (this is to prevent weirdness when zooming in).
        cx=2*(cx/2)
        cy=2*(cy/2)
        placements=[]
        fail1=False	# At least one placement at this level failed - typically cos imagery not available at this location/level
        pending=False	# At least one placement at this level is still loading
        prioritybase=(1+self.provider_levelmax-level)*100
        seq=self.spiral(6,6)
        for i in range(len(seq)):
            (x,y)=seq[i]
            placement=self.getplacement(cx+x,cy+y,level,prioritybase+i,True)		# Initiate fetch
            if not placement:
                fail1=True
            elif not placement.islaidout():
                pending=True
            else:
                placements.append(placement)

        if __debug__: print level, fail1, pending
        if (fail1 or pending) and level>levelmin:
            # Not all imagery currently available at desired level. Go up and get 3x3 tiles around the centre tile.
            level-=1
            cx/=2
            cy/=2
            fail2=False
            prioritybase+=100
            seq=self.spiral(3,3)
            for i in range(len(seq)):
                (x,y)=seq[i]
                placement=self.getplacement(cx+x,cy+y,level,prioritybase+i,fail1)	# Initiate fetch of imagery only if desired level failed
                if not (placement and placement.islaidout()):
                    fail2=fail1
                else:
                    placements.insert(0,placement)	# Insert at start so drawn under higher-level

            while fail2 and level>levelmin:
                # Not all imagery available at higher level either. Go up and get 3x3 tiles around the centre tile.
                level-=1
                cx/=2
                cy/=2
                #cx=(cx-1)/2
                #cy=(cy-1)/2
                fail2=False
                prioritybase+=100
                for i in range(len(seq)):
                    (x,y)=seq[i]
                    placement=self.getplacement(cx+x,cy+y,level,prioritybase+i,fail1)	# Initiate fetch of imagery only if desired level failed
                    if not (placement and placement.islaidout()):
                        fail2=fail1
                    else:
                        placements.insert(0,placement)	# Insert at start so drawn under higher-level

        if __debug__: print "Actual imagery level", level
        return placements


    # Helper to return coordinates in a spiral from http://stackoverflow.com/questions/398299/looping-in-a-spiral
    def spiral(self, N, M):
        x = y = 0
        dx, dy = 0, -1
        retval=[]
        for dumb in xrange(N*M):
            if abs(x) == abs(y) and [dx,dy] != [1,0] or x>0 and y == 1-x:
                dx, dy = -dy, dx            # corner, change direction
            if abs(x)>N/2 or abs(y)>M/2:    # non-square
                dx, dy = -dy, dx            # change direction
                x, y = -y+dx, x+dy          # jump
            retval.append((x,y))
            x, y = x+dx, y+dy
        return retval


    # Returns a laid-out placement if possible, or not laid-out if image is still loading, or None if not available.
    def getplacement(self,x,y,level,priority,fetch):
        quadkey=self.bing_quadkey(x,y,level)
        url=self.provider_url % quadkey
        name=basename(url).split('?')[0]
        if name in self.placementcache:
            # Already created
            placement=self.placementcache[name]
        elif fetch:
            # Make a new one. We could also do this if the image file is available, but don't since layout is expensive.
            (north,west)=self.xy2latlon(x,y,level)
            (south,east)=self.xy2latlon(x+1,y+1,level)
            placement=DrapedImage(name, 65535, [[(west,north,0,1),(east,north,1,1),(east,south,1,0),(west,south,0,0)]])
            placement.load(self.canvas.lookup, self.canvas.defs, self.canvas.vertexcache)
            self.placementcache[name]=placement
            # Initiate fetch of image and do layout. Prioritise more detail.
            self.q.put((priority, self.initplacement, (placement,name,url)))
        else:
            placement=None

        # Load it if it's not loaded but is ready to be
        if placement and not placement.islaidout() and Polygon.islaidout(placement):
            try:
                if __debug__: clock=time.clock()
                filename=self.filecache.get(name)	# downloaded image or None
                self.canvas.vertexcache.allocate_dynamic(placement, True)	# couldn't do this in thread context
                placement.definition.texture=self.canvas.vertexcache.texcache.get(filename, False, False)	# discard alpha
                if __debug__: print "%6.3f time in imagery load   for %s" % (time.clock()-clock, placement.name)
                assert placement.islaidout()
            except:
                if __debug__: print_exc()
                # Some failure - perhaps corrupted image?
                placement.clearlayout(self.canvas.vertexcache)
                placement=None
                self.placementcache[name]=None

        return placement


    def latlon2xy(self, lat, lon, level):
        ntiles=2**level				# number of tiles per axis at this level
        sinlat=sin(radians(lat))
        x = int((lon+180) * ntiles/360.0)
        y = int( (0.5 - (log((1+sinlat)/(1-sinlat)) / fourpi)) * ntiles)
        return (x,y)


    def xy2latlon(self, x, y, level):
        ntiles=float(2**level)			# number of tiles per axis at this level
        lat = 90 - 360 * atan(exp((y/ntiles-0.5)*2*pi)) / pi
        lon = x*360.0/ntiles - 180
        return (lat,lon)


    def bing_quadkey(self, x, y, level):
        # http://msdn.microsoft.com/en-us/library/bb259689.aspx
        i=level
        quadkey=''
        while i>0:
            digit=0
            mask = 1 << (i-1)
            if (x & mask):
                digit+=1
            if (y & mask):
                digit+=2
            quadkey+=('%d' % digit)
            i-=1
        return quadkey


    # Called in worker thread - don't do anything fancy since main body of code is not thread-safe
    def bing_setup(self, tls):
        try:
            key='AhATjCXv4Sb-i_YKsa_8lF4DtHwVoicFxl0Stc9QiXZNywFbI2rajKZCsLFIMOX2'
            h=urlopen('http://dev.virtualearth.net/REST/v1/Imagery/Metadata/Aerial?key=%s' % key)
            d=h.read()
            h.close()
            info=json_decode(d)
            # http://msdn.microsoft.com/en-us/library/ff701707.aspx
            if 'authenticationResultCode' not in info or info['authenticationResultCode']!='ValidCredentials' or 'statusCode' not in info or info['statusCode']!=200:
                return
            res=info['resourceSets'][0]['resources'][0]
            # http://msdn.microsoft.com/en-us/library/ff701712.aspx
            self.provider_levelmin=int(res['zoomMin'])
            self.provider_levelmax=int(res['zoomMax'])
            self.provider_url=res['imageUrl'].replace('{subdomain}',res['imageUrlSubdomains'][-1]).replace('{culture}','en').replace('{quadkey}','%s') + '&key=' + key	# was random.choice(res['imageUrlSubdomains']) but always picking the same server seems to give better caching
        except:
            if __debug__: print_exc()
        try:
            if info['brandLogoUri']:
                filename=self.filecache.fetch(basename(info['brandLogoUri']), info['brandLogoUri'])
                image = PIL.Image.open(filename)	# yuck. but at least open is lazy
                self.provider_logo=(filename,image.size[0],image.size[1])
        except:
            if __debug__: print_exc()
        self.canvas.Refresh()	# Might have been waiting on this to get imagery


    # Called in worker thread - fetches image and does placement layout (which uses it's own tessellator and so is thread-safe).
    # don't do anything fancy since main body of code is not thread-safe
    def initplacement(self, tls, placement, name, url):
        filename=self.filecache.fetch(name, url)
        if not filename:
            # Couldn't fetch image - remove corresponding placement
            self.placementcache[name]=None
        else:
            if __debug__: clock=time.clock()
            placement.layout(self.tile, self.canvas.options, self.canvas.vertexcache, tls=tls)
            if not placement.dynamic_data.size:
                if __debug__: print "DrapedImage layout failed for %s - no tris" % placement.name
                self.placementcache[name]=None
            elif __debug__:
                print "%6.3f time in imagery layout for %s" % (time.clock()-clock, placement.name)
        self.canvas.Refresh()	# Probably wanting to display this - corresponding placement will be loaded and laid out during OnPaint


# Python 2.5 doesn't have json module, so here's a quick and dirty decoder. Doesn't santise input.
def json_decode(s):
    null=None
    if not s.startswith('{'): return {}
    return eval(s.decode('utf-8').replace('\/','/'))

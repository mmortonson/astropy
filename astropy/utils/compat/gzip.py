# Licensed under a 3-clause BSD style license - see LICENSE.rst
"""
A backport of Python 3.2's gzip.py for use in place of the broken one
in Python 3.1.
"""

from __future__ import absolute_import

import sys
if sys.version_info[:2] == (3, 1):
    """Functions that read and write gzipped files.

    The user of the file doesn't have to worry about the compression,
    but random access is not allowed."""

    # based on Andrew Kuchling's minigzip.py distributed with the zlib module

    import struct, sys, time, os
    import zlib
    import builtins
    import io

    __all__ = ["GzipFile", "open", "compress", "decompress"]

    FTEXT, FHCRC, FEXTRA, FNAME, FCOMMENT = 1, 2, 4, 8, 16

    READ, WRITE = 1, 2

    def U32(i):
        """Return i as an unsigned integer, assuming it fits in 32 bits.
        If it's >= 2GB when viewed as a 32-bit unsigned int, return a long.
        """
        if i < 0:
            i += 1 << 32
        return i

    def LOWU32(i):
        """Return the low-order 32 bits, as a non-negative int"""
        return i & 0xFFFFFFFF

    def write32u(output, value):
        # The L format writes the bit pattern correctly whether signed
        # or unsigned.
        output.write(struct.pack("<L", value))

    def read32(input):
        return struct.unpack("<I", input.read(4))[0]

    def open(filename, mode="rb", compresslevel=9):
        """Shorthand for GzipFile(filename, mode, compresslevel).

        The filename argument is required; mode defaults to 'rb'
        and compresslevel defaults to 9.

        """
        return GzipFile(filename, mode, compresslevel)

    class _PaddedFile:
        """Minimal read-only file object that prepends a string to the contents
        of an actual file. Shouldn't be used outside of gzip.py, as it lacks
        essential functionality."""

        def __init__(self, f, prepend=b''):
            self._buffer = prepend
            self._length = len(prepend)
            self.file = f
            self._read = 0

        def read(self, size):
            if self._read is None:
                return self.file.read(size)
            if self._read + size <= self._length:
                read = self._read
                self._read += size
                return self._buffer[read:self._read]
            else:
                read = self._read
                self._read = None
                return self._buffer[read:] + \
                       self.file.read(size-self._length+read)

        def prepend(self, prepend=b'', readprevious=False):
            if self._read is None:
                self._buffer = prepend
            elif readprevious and len(prepend) <= self._read:
                self._read -= len(prepend)
                return
            else:
                self._buffer = self._buffer[read:] + prepend
            self._length = len(self._buffer)
            self._read = 0

        def unused(self):
            if self._read is None:
                return b''
            return self._buffer[self._read:]

        def seek(self, offset, whence=0):
            # This is only ever called with offset=whence=0
            if whence == 1 and self._read is not None:
                if 0 <= offset + self._read <= self._length:
                    self._read += offset
                    return
                else:
                    offset += self._length - self._read
            self._read = None
            self._buffer = None
            return self.file.seek(offset, whence)

        def __getattr__(self, name):
            return getattr(self.file, name)


    class GzipFile(io.BufferedIOBase):
        """The GzipFile class simulates most of the methods of a file object with
        the exception of the readinto() and truncate() methods.

        """

        myfileobj = None
        max_read_chunk = 10 * 1024 * 1024   # 10Mb

        def __init__(self, filename=None, mode=None,
                     compresslevel=9, fileobj=None, mtime=None):
            """Constructor for the GzipFile class.

            At least one of fileobj and filename must be given a
            non-trivial value.

            The new class instance is based on fileobj, which can be a regular
            file, a StringIO object, or any other object which simulates a file.
            It defaults to None, in which case filename is opened to provide
            a file object.

            When fileobj is not None, the filename argument is only used to be
            included in the gzip file header, which may includes the original
            filename of the uncompressed file.  It defaults to the filename of
            fileobj, if discernible; otherwise, it defaults to the empty string,
            and in this case the original filename is not included in the header.

            The mode argument can be any of 'r', 'rb', 'a', 'ab', 'w', or 'wb',
            depending on whether the file will be read or written.  The default
            is the mode of fileobj if discernible; otherwise, the default is 'rb'.
            Be aware that only the 'rb', 'ab', and 'wb' values should be used
            for cross-platform portability.

            The compresslevel argument is an integer from 1 to 9 controlling the
            level of compression; 1 is fastest and produces the least compression,
            and 9 is slowest and produces the most compression.  The default is 9.

            The mtime argument is an optional numeric timestamp to be written
            to the stream when compressing.  All gzip compressed streams
            are required to contain a timestamp.  If omitted or None, the
            current time is used.  This module ignores the timestamp when
            decompressing; however, some programs, such as gunzip, make use
            of it.  The format of the timestamp is the same as that of the
            return value of time.time() and of the st_mtime member of the
            object returned by os.stat().

            """

            # guarantee the file is opened in binary mode on platforms
            # that care about that sort of thing
            if mode and 'b' not in mode:
                mode += 'b'
            if fileobj is None:
                fileobj = self.myfileobj = builtins.open(filename, mode or 'rb')
            if filename is None:
                if hasattr(fileobj, 'name'): filename = fileobj.name
                else: filename = ''
            if mode is None:
                if hasattr(fileobj, 'mode'): mode = fileobj.mode
                else: mode = 'rb'

            if mode[0:1] == 'r':
                self.mode = READ
                # Set flag indicating start of a new member
                self._new_member = True
                # Buffer data read from gzip file. extrastart is offset in
                # stream where buffer starts. extrasize is number of
                # bytes remaining in buffer from current stream position.
                self.extrabuf = b""
                self.extrasize = 0
                self.extrastart = 0
                self.name = filename
                # Starts small, scales exponentially
                self.min_readsize = 100
                fileobj = _PaddedFile(fileobj)

            elif mode[0:1] == 'w' or mode[0:1] == 'a':
                self.mode = WRITE
                self._init_write(filename)
                self.compress = zlib.compressobj(compresslevel,
                                                 zlib.DEFLATED,
                                                 -zlib.MAX_WBITS,
                                                 zlib.DEF_MEM_LEVEL,
                                                 0)
            else:
                raise IOError("Mode " + mode + " not supported")

            self.fileobj = fileobj
            self.offset = 0
            self.mtime = mtime

            if self.mode == WRITE:
                self._write_gzip_header()

        @property
        def filename(self):
            import warnings
            warnings.warn("use the name attribute", DeprecationWarning, 2)
            if self.mode == WRITE and self.name[-3:] != ".gz":
                return self.name + ".gz"
            return self.name

        def __repr__(self):
            fileobj = self.fileobj
            if isinstance(fileobj, _PaddedFile):
                fileobj = fileobj.file
            s = repr(fileobj)
            return '<gzip ' + s[1:-1] + ' ' + hex(id(self)) + '>'

        def _check_closed(self):
            """Raises a ValueError if the underlying file object has been closed.

            """
            if self.closed:
                raise ValueError('I/O operation on closed file.')

        def _init_write(self, filename):
            self.name = filename
            self.crc = zlib.crc32(b"") & 0xffffffff
            self.size = 0
            self.writebuf = []
            self.bufsize = 0

        def _write_gzip_header(self):
            self.fileobj.write(b'\037\213')             # magic header
            self.fileobj.write(b'\010')                 # compression method
            try:
                # RFC 1952 requires the FNAME field to be Latin-1. Do not
                # include filenames that cannot be represented that way.
                fname = os.path.basename(self.name)
                fname = fname.encode('latin-1')
                if fname.endswith(b'.gz'):
                    fname = fname[:-3]
            except UnicodeEncodeError:
                fname = b''
            flags = 0
            if fname:
                flags = FNAME
            self.fileobj.write(chr(flags).encode('latin-1'))
            mtime = self.mtime
            if mtime is None:
                mtime = time.time()
            write32u(self.fileobj, int(mtime))
            self.fileobj.write(b'\002')
            self.fileobj.write(b'\377')
            if fname:
                self.fileobj.write(fname + b'\000')

        def _init_read(self):
            self.crc = zlib.crc32(b"") & 0xffffffff
            self.size = 0

        def _read_gzip_header(self):
            magic = self.fileobj.read(2)
            if magic == b'':
                raise EOFError("Reached EOF")

            if magic != b'\037\213':
                raise IOError('Not a gzipped file')
            method = ord( self.fileobj.read(1) )
            if method != 8:
                raise IOError('Unknown compression method')
            flag = ord( self.fileobj.read(1) )
            self.mtime = read32(self.fileobj)
            # extraflag = self.fileobj.read(1)
            # os = self.fileobj.read(1)
            self.fileobj.read(2)

            if flag & FEXTRA:
                # Read & discard the extra field, if present
                xlen = ord(self.fileobj.read(1))
                xlen = xlen + 256*ord(self.fileobj.read(1))
                self.fileobj.read(xlen)
            if flag & FNAME:
                # Read and discard a null-terminated string containing the filename
                while True:
                    s = self.fileobj.read(1)
                    if not s or s==b'\000':
                        break
            if flag & FCOMMENT:
                # Read and discard a null-terminated string containing a comment
                while True:
                    s = self.fileobj.read(1)
                    if not s or s==b'\000':
                        break
            if flag & FHCRC:
                self.fileobj.read(2)     # Read & discard the 16-bit header CRC

            unused = self.fileobj.unused()
            if unused:
                uncompress = self.decompress.decompress(unused)
                self._add_read_data(uncompress)

        def write(self,data):
            self._check_closed()
            if self.mode != WRITE:
                import errno
                raise IOError(errno.EBADF, "write() on read-only GzipFile object")

            if self.fileobj is None:
                raise ValueError("write() on closed GzipFile object")

            # Convert data type if called by io.BufferedWriter.
            if isinstance(data, memoryview):
                data = data.tobytes()

            if len(data) > 0:
                self.size = self.size + len(data)
                self.crc = zlib.crc32(data, self.crc) & 0xffffffff
                self.fileobj.write( self.compress.compress(data) )
                self.offset += len(data)

            return len(data)

        def read(self, size=-1):
            self._check_closed()
            if self.mode != READ:
                import errno
                raise IOError(errno.EBADF, "read() on write-only GzipFile object")

            if self.extrasize <= 0 and self.fileobj is None:
                return b''

            readsize = 1024
            if size < 0:        # get the whole thing
                try:
                    while True:
                        self._read(readsize)
                        readsize = min(self.max_read_chunk, readsize * 2)
                except EOFError:
                    size = self.extrasize
            else:               # just get some more of it
                try:
                    while size > self.extrasize:
                        self._read(readsize)
                        readsize = min(self.max_read_chunk, readsize * 2)
                except EOFError:
                    if size > self.extrasize:
                        size = self.extrasize

            offset = self.offset - self.extrastart
            chunk = self.extrabuf[offset: offset + size]
            self.extrasize = self.extrasize - size

            self.offset += size
            return chunk

        def peek(self, n):
            if self.mode != READ:
                import errno
                raise IOError(errno.EBADF, "peek() on write-only GzipFile object")

            # Do not return ridiculously small buffers, for one common idiom
            # is to call peek(1) and expect more bytes in return.
            if n < 100:
                n = 100
            if self.extrasize == 0:
                if self.fileobj is None:
                    return b''
                try:
                    # 1024 is the same buffering heuristic used in read()
                    self._read(max(n, 1024))
                except EOFError:
                    pass
            offset = self.offset - self.extrastart
            remaining = self.extrasize
            assert remaining == len(self.extrabuf) - offset
            return self.extrabuf[offset:offset + n]

        def _unread(self, buf):
            self.extrasize = len(buf) + self.extrasize
            self.offset -= len(buf)

        def _read(self, size=1024):
            if self.fileobj is None:
                raise EOFError("Reached EOF")

            if self._new_member:
                # If the _new_member flag is set, we have to
                # jump to the next member, if there is one.
                self._init_read()
                self._read_gzip_header()
                self.decompress = zlib.decompressobj(-zlib.MAX_WBITS)
                self._new_member = False

            # Read a chunk of data from the file
            buf = self.fileobj.read(size)

            # If the EOF has been reached, flush the decompression object
            # and mark this object as finished.

            if buf == b"":
                uncompress = self.decompress.flush()
                # Prepend the already read bytes to the fileobj to they can be
                # seen by _read_eof()
                self.fileobj.prepend(self.decompress.unused_data, True)
                self._read_eof()
                self._add_read_data( uncompress )
                raise EOFError('Reached EOF')

            uncompress = self.decompress.decompress(buf)
            self._add_read_data( uncompress )

            if self.decompress.unused_data != b"":
                # Ending case: we've come to the end of a member in the file,
                # so seek back to the start of the unused data, finish up
                # this member, and read a new gzip header.
                # Prepend the already read bytes to the fileobj to they can be
                # seen by _read_eof() and _read_gzip_header()
                self.fileobj.prepend(self.decompress.unused_data, True)
                # Check the CRC and file size, and set the flag so we read
                # a new member on the next call
                self._read_eof()
                self._new_member = True

        def _add_read_data(self, data):
            self.crc = zlib.crc32(data, self.crc) & 0xffffffff
            offset = self.offset - self.extrastart
            self.extrabuf = self.extrabuf[offset:] + data
            self.extrasize = self.extrasize + len(data)
            self.extrastart = self.offset
            self.size = self.size + len(data)

        def _read_eof(self):
            # We've read to the end of the file
            # We check the that the computed CRC and size of the
            # uncompressed data matches the stored values.  Note that the size
            # stored is the true file size mod 2**32.
            crc32 = read32(self.fileobj)
            isize = read32(self.fileobj)  # may exceed 2GB
            if crc32 != self.crc:
                raise IOError("CRC check failed %s != %s" % (hex(crc32),
                                                             hex(self.crc)))
            elif isize != (self.size & 0xffffffff):
                raise IOError("Incorrect length of data produced")

            # Gzip files can be padded with zeroes and still have archives.
            # Consume all zero bytes and set the file position to the first
            # non-zero byte. See http://www.gzip.org/#faq8
            c = b"\x00"
            while c == b"\x00":
                c = self.fileobj.read(1)
            if c:
                self.fileobj.prepend(c, True)

        @property
        def closed(self):
            return self.fileobj is None

        def close(self):
            if self.fileobj is None:
                return
            if self.mode == WRITE:
                self.fileobj.write(self.compress.flush())
                write32u(self.fileobj, self.crc)
                # self.size may exceed 2GB, or even 4GB
                write32u(self.fileobj, self.size & 0xffffffff)
                self.fileobj = None
            elif self.mode == READ:
                self.fileobj = None
            if self.myfileobj:
                self.myfileobj.close()
                self.myfileobj = None

        def flush(self,zlib_mode=zlib.Z_SYNC_FLUSH):
            self._check_closed()
            if self.mode == WRITE:
                # Ensure the compressor's buffer is flushed
                self.fileobj.write(self.compress.flush(zlib_mode))
                self.fileobj.flush()

        def fileno(self):
            """Invoke the underlying file object's fileno() method.

            This will raise AttributeError if the underlying file object
            doesn't support fileno().
            """
            return self.fileobj.fileno()

        def rewind(self):
            '''Return the uncompressed stream file position indicator to the
            beginning of the file'''
            if self.mode != READ:
                raise IOError("Can't rewind in write mode")
            self.fileobj.seek(0)
            self._new_member = True
            self.extrabuf = b""
            self.extrasize = 0
            self.extrastart = 0
            self.offset = 0

        def readable(self):
            return self.mode == READ

        def writable(self):
            return self.mode == WRITE

        def seekable(self):
            return True

        def seek(self, offset, whence=0):
            if whence:
                if whence == 1:
                    offset = self.offset + offset
                else:
                    raise ValueError('Seek from end not supported')
            if self.mode == WRITE:
                if offset < self.offset:
                    raise IOError('Negative seek in write mode')
                count = offset - self.offset
                chunk = bytes(1024)
                for i in range(count // 1024):
                    self.write(chunk)
                self.write(bytes(count % 1024))
            elif self.mode == READ:
                if offset < self.offset:
                    # for negative seek, rewind and do positive seek
                    self.rewind()
                count = offset - self.offset
                for i in range(count // 1024):
                    self.read(1024)
                self.read(count % 1024)

            return self.offset

        def readline(self, size=-1):
            if size < 0:
                # Shortcut common case - newline found in buffer.
                offset = self.offset - self.extrastart
                i = self.extrabuf.find(b'\n', offset) + 1
                if i > 0:
                    self.extrasize -= i - offset
                    self.offset += i - offset
                    return self.extrabuf[offset: i]

                size = sys.maxsize
                readsize = self.min_readsize
            else:
                readsize = size
            bufs = []
            while size != 0:
                c = self.read(readsize)
                i = c.find(b'\n')

                # We set i=size to break out of the loop under two
                # conditions: 1) there's no newline, and the chunk is
                # larger than size, or 2) there is a newline, but the
                # resulting line would be longer than 'size'.
                if (size <= i) or (i == -1 and len(c) > size):
                    i = size - 1

                if i >= 0 or c == b'':
                    bufs.append(c[:i + 1])    # Add portion of last chunk
                    self._unread(c[i + 1:])   # Push back rest of chunk
                    break

                # Append chunk to list, decrease 'size',
                bufs.append(c)
                size = size - len(c)
                readsize = min(size, readsize * 2)
            if readsize > self.min_readsize:
                self.min_readsize = min(readsize, self.min_readsize * 2, 512)
            return b''.join(bufs) # Return resulting line


    def compress(data, compresslevel=9):
        """Compress data in one shot and return the compressed string.
        Optional argument is the compression level, in range of 1-9.
        """
        buf = io.BytesIO()
        with GzipFile(fileobj=buf, mode='wb', compresslevel=compresslevel) as f:
            f.write(data)
        return buf.getvalue()

    def decompress(data):
        """Decompress a gzip compressed string in one shot.
        Return the decompressed string.
        """
        with GzipFile(fileobj=io.BytesIO(data)) as f:
            return f.read()

else:
    from gzip import *
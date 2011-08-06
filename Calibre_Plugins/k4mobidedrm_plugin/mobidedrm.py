#!/usr/bin/python
#
# This is a python script. You need a Python interpreter to run it.
# For example, ActiveState Python, which exists for windows.
#
# Changelog
#  0.01 - Initial version
#  0.02 - Huffdic compressed books were not properly decrypted
#  0.03 - Wasn't checking MOBI header length
#  0.04 - Wasn't sanity checking size of data record
#  0.05 - It seems that the extra data flags take two bytes not four
#  0.06 - And that low bit does mean something after all :-)
#  0.07 - The extra data flags aren't present in MOBI header < 0xE8 in size
#  0.08 - ...and also not in Mobi header version < 6
#  0.09 - ...but they are there with Mobi header version 6, header size 0xE4!
#  0.10 - Outputs unencrypted files as-is, so that when run as a Calibre
#         import filter it works when importing unencrypted files.
#         Also now handles encrypted files that don't need a specific PID.
#  0.11 - use autoflushed stdout and proper return values
#  0.12 - Fix for problems with metadata import as Calibre plugin, report errors
#  0.13 - Formatting fixes: retabbed file, removed trailing whitespace
#         and extra blank lines, converted CR/LF pairs at ends of each line,
#         and other cosmetic fixes.
#  0.14 - Working out when the extra data flags are present has been problematic
#         Versions 7 through 9 have tried to tweak the conditions, but have been
#         only partially successful. Closer examination of lots of sample
#         files reveals that a confusion has arisen because trailing data entries
#         are not encrypted, but it turns out that the multibyte entries
#         in utf8 file are encrypted. (Although neither kind gets compressed.)
#         This knowledge leads to a simplification of the test for the 
#         trailing data byte flags - version 5 and higher AND header size >= 0xE4. 
#  0.15 - Now outputs 'heartbeat', and is also quicker for long files.
#  0.16 - And reverts to 'done' not 'done.' at the end for unswindle compatibility.
#  0.17 - added modifications to support its use as an imported python module
#         both inside calibre and also in other places (ie K4DeDRM tools)
#  0.17a- disabled the standalone plugin feature since a plugin can not import
#         a plugin
#  0.18 - It seems that multibyte entries aren't encrypted in a v7 file...
#         Removed the disabled Calibre plug-in code
#         Permit use of 8-digit PIDs
#  0.19 - It seems that multibyte entries aren't encrypted in a v6 file either.
#  0.20 - Correction: It seems that multibyte entries are encrypted in a v6 file.
#  0.21 - Added support for multiple pids
#  0.22 - revised structure to hold MobiBook as a class to allow an extended interface
#  0.23 - fixed problem with older files with no EXTH section 
#  0.24 - add support for type 1 encryption and 'TEXtREAd' books as well
#  0.25 - Fixed support for 'BOOKMOBI' type 1 encryption
#  0.26 - Now enables Text-To-Speech flag and sets clipping limit to 100%
#  0.27 - Correct pid metadata token generation to match that used by skindle (Thank You Bart!)
#  0.28 - slight additional changes to metadata token generation (None -> '')
#  0.29 - It seems that the ideas about when multibyte trailing characters were
#         included in the encryption were wrong. They are for DOC compressed
#         files, but they are not for HUFF/CDIC compress files!
#  0.30 - Modified interface slightly to work better with new calibre plugin style
#  0.31 - The multibyte encrytion info is true for version 7 files too.

__version__ = '0.31'

import sys

class Unbuffered:
    def __init__(self, stream):
        self.stream = stream
    def write(self, data):
        self.stream.write(data)
        self.stream.flush()
    def __getattr__(self, attr):
        return getattr(self.stream, attr)
sys.stdout=Unbuffered(sys.stdout)

import os
import struct
import binascii

class DrmException(Exception):
    pass


#
# MobiBook Utility Routines
#

# Implementation of Pukall Cipher 1
def PC1(key, src, decryption=True):
    sum1 = 0;
    sum2 = 0;
    keyXorVal = 0;
    if len(key)!=16:
        print "Bad key length!"
        return None
    wkey = []
    for i in xrange(8):
        wkey.append(ord(key[i*2])<<8 | ord(key[i*2+1]))
    dst = ""
    for i in xrange(len(src)):
        temp1 = 0;
        byteXorVal = 0;
        for j in xrange(8):
            temp1 ^= wkey[j]
            sum2  = (sum2+j)*20021 + sum1
            sum1  = (temp1*346)&0xFFFF
            sum2  = (sum2+sum1)&0xFFFF
            temp1 = (temp1*20021+1)&0xFFFF
            byteXorVal ^= temp1 ^ sum2
        curByte = ord(src[i])
        if not decryption:
            keyXorVal = curByte * 257;
        curByte = ((curByte ^ (byteXorVal >> 8)) ^ byteXorVal) & 0xFF
        if decryption:
            keyXorVal = curByte * 257;
        for j in xrange(8):
            wkey[j] ^= keyXorVal;
        dst+=chr(curByte)
    return dst

def checksumPid(s):
    letters = "ABCDEFGHIJKLMNPQRSTUVWXYZ123456789"
    crc = (~binascii.crc32(s,-1))&0xFFFFFFFF
    crc = crc ^ (crc >> 16)
    res = s
    l = len(letters)
    for i in (0,1):
        b = crc & 0xff
        pos = (b // l) ^ (b % l)
        res += letters[pos%l]
        crc >>= 8
    return res

def getSizeOfTrailingDataEntries(ptr, size, flags):
    def getSizeOfTrailingDataEntry(ptr, size):
        bitpos, result = 0, 0
        if size <= 0:
            return result
        while True:
            v = ord(ptr[size-1])
            result |= (v & 0x7F) << bitpos
            bitpos += 7
            size -= 1
            if (v & 0x80) != 0 or (bitpos >= 28) or (size == 0):
                return result
    num = 0
    testflags = flags >> 1
    while testflags:
        if testflags & 1:
            num += getSizeOfTrailingDataEntry(ptr, size - num)
        testflags >>= 1
    # Check the low bit to see if there's multibyte data present.
    # if multibyte data is included in the encryped data, we'll
    # have already cleared this flag.
    if flags & 1:
        num += (ord(ptr[size - num - 1]) & 0x3) + 1
    return num



class MobiBook:
    def loadSection(self, section):
        if (section + 1 == self.num_sections):
            endoff = len(self.data_file)
        else:
            endoff = self.sections[section + 1][0]
        off = self.sections[section][0]
        return self.data_file[off:endoff]

    def __init__(self, infile):
        # initial sanity check on file
        self.data_file = file(infile, 'rb').read()
        self.mobi_data = ''
        self.header = self.data_file[0:78]
        if self.header[0x3C:0x3C+8] != 'BOOKMOBI' and self.header[0x3C:0x3C+8] != 'TEXtREAd':
            raise DrmException("invalid file format")
        self.magic = self.header[0x3C:0x3C+8]
        self.crypto_type = -1

        # build up section offset and flag info
        self.num_sections, = struct.unpack('>H', self.header[76:78])
        self.sections = []
        for i in xrange(self.num_sections):
            offset, a1,a2,a3,a4 = struct.unpack('>LBBBB', self.data_file[78+i*8:78+i*8+8])
            flags, val = a1, a2<<16|a3<<8|a4
            self.sections.append( (offset, flags, val) )

        # parse information from section 0
        self.sect = self.loadSection(0)
        self.records, = struct.unpack('>H', self.sect[0x8:0x8+2])
        self.compression, = struct.unpack('>H', self.sect[0x0:0x0+2])

        if self.magic == 'TEXtREAd':
            print "Book has format: ", self.magic
            self.extra_data_flags = 0
            self.mobi_length = 0
            self.mobi_version = -1
            self.meta_array = {}
            return
        self.mobi_length, = struct.unpack('>L',self.sect[0x14:0x18])
        self.mobi_version, = struct.unpack('>L',self.sect[0x68:0x6C])
        print "MOBI header version = %d, length = %d" %(self.mobi_version, self.mobi_length)
        self.extra_data_flags = 0
        if (self.mobi_length >= 0xE4) and (self.mobi_version >= 5):
            self.extra_data_flags, = struct.unpack('>H', self.sect[0xF2:0xF4])
            print "Extra Data Flags = %d" % self.extra_data_flags
        if (self.compression != 17480):
            # multibyte utf8 data is included in the encryption for PalmDoc compression
            # so clear that byte so that we leave it to be decrypted.
            self.extra_data_flags &= 0xFFFE

        # if exth region exists parse it for metadata array
        self.meta_array = {}
        try:
            exth_flag, = struct.unpack('>L', self.sect[0x80:0x84])
            exth = 'NONE'
            if exth_flag & 0x40:
                exth = self.sect[16 + self.mobi_length:]
            if (len(exth) >= 4) and (exth[:4] == 'EXTH'):
                nitems, = struct.unpack('>I', exth[8:12])
                pos = 12
                for i in xrange(nitems):
                    type, size = struct.unpack('>II', exth[pos: pos + 8])
                    content = exth[pos + 8: pos + size]
                    self.meta_array[type] = content
                    # reset the text to speech flag and clipping limit, if present
                    if type == 401 and size == 9:
                        # set clipping limit to 100%
                        self.patchSection(0, "\144", 16 + self.mobi_length + pos + 8)
                    elif type == 404 and size == 9:
                        # make sure text to speech is enabled
                        self.patchSection(0, "\0", 16 + self.mobi_length + pos + 8)
                    # print type, size, content, content.encode('hex')
                    pos += size
        except:
            self.meta_array = {}
            pass
            
    def getBookTitle(self):
        title = ''
        if 503 in self.meta_array:
            title = self.meta_array[503]
        else :
            toff, tlen = struct.unpack('>II', self.sect[0x54:0x5c])
            tend = toff + tlen
            title = self.sect[toff:tend]
        if title == '':
            title = self.header[:32]
            title = title.split("\0")[0]
        return title

    def getPIDMetaInfo(self):
        rec209 = ''
        token = ''
        if 209 in self.meta_array:
            rec209 = self.meta_array[209]
            data = rec209
            # The 209 data comes in five byte groups. Interpret the last four bytes
            # of each group as a big endian unsigned integer to get a key value
            # if that key exists in the meta_array, append its contents to the token
            for i in xrange(0,len(data),5):
                val,  = struct.unpack('>I',data[i+1:i+5])
                sval = self.meta_array.get(val,'')
                token += sval
        return rec209, token

    def patch(self, off, new):
        self.data_file = self.data_file[:off] + new + self.data_file[off+len(new):]

    def patchSection(self, section, new, in_off = 0):
        if (section + 1 == self.num_sections):
            endoff = len(self.data_file)
        else:
            endoff = self.sections[section + 1][0]
        off = self.sections[section][0]
        assert off + in_off + len(new) <= endoff
        self.patch(off + in_off, new)

    def parseDRM(self, data, count, pidlist):
        found_key = None
        keyvec1 = "\x72\x38\x33\xB0\xB4\xF2\xE3\xCA\xDF\x09\x01\xD6\xE2\xE0\x3F\x96"
        for pid in pidlist:
            bigpid = pid.ljust(16,'\0')
            temp_key = PC1(keyvec1, bigpid, False)
            temp_key_sum = sum(map(ord,temp_key)) & 0xff
            found_key = None
            for i in xrange(count):
                verification, size, type, cksum, cookie = struct.unpack('>LLLBxxx32s', data[i*0x30:i*0x30+0x30])
                if cksum == temp_key_sum:
                    cookie = PC1(temp_key, cookie)
                    ver,flags,finalkey,expiry,expiry2 = struct.unpack('>LL16sLL', cookie)
                    if verification == ver and (flags & 0x1F) == 1:
                        found_key = finalkey
                        break
            if found_key != None:
                break
        if not found_key:
            # Then try the default encoding that doesn't require a PID
            pid = "00000000"
            temp_key = keyvec1
            temp_key_sum = sum(map(ord,temp_key)) & 0xff
            for i in xrange(count):
                verification, size, type, cksum, cookie = struct.unpack('>LLLBxxx32s', data[i*0x30:i*0x30+0x30])
                if cksum == temp_key_sum:
                    cookie = PC1(temp_key, cookie)
                    ver,flags,finalkey,expiry,expiry2 = struct.unpack('>LL16sLL', cookie)
                    if verification == ver:
                        found_key = finalkey
                        break
        return [found_key,pid]

    def getMobiFile(self, outpath):
        file(outpath,'wb').write(self.mobi_data)

    def processBook(self, pidlist):
        crypto_type, = struct.unpack('>H', self.sect[0xC:0xC+2])
        print 'Crypto Type is: ', crypto_type
        self.crypto_type = crypto_type
        if crypto_type == 0:
            print "This book is not encrypted."
            self.mobi_data = self.data_file
            return
        if crypto_type != 2 and crypto_type != 1:
            raise DrmException("Cannot decode unknown Mobipocket encryption type %d" % crypto_type)

        goodpids = []
        for pid in pidlist:
            if len(pid)==10:
                if checksumPid(pid[0:-2]) != pid:
                    print "Warning: PID " + pid + " has incorrect checksum, should have been "+checksumPid(pid[0:-2])
                goodpids.append(pid[0:-2])
            elif len(pid)==8:
                goodpids.append(pid)

        if self.crypto_type == 1:
            t1_keyvec = "QDCVEPMU675RUBSZ"
            if self.magic == 'TEXtREAd':
                bookkey_data = self.sect[0x0E:0x0E+16]
            elif self.mobi_version < 0:
                bookkey_data = self.sect[0x90:0x90+16] 
            else:
                bookkey_data = self.sect[self.mobi_length+16:self.mobi_length+32] 
            pid = "00000000"
            found_key = PC1(t1_keyvec, bookkey_data)
        else :
            # calculate the keys
            drm_ptr, drm_count, drm_size, drm_flags = struct.unpack('>LLLL', self.sect[0xA8:0xA8+16])
            if drm_count == 0:
                raise DrmException("Not yet initialised with PID. Must be opened with Mobipocket Reader first.")
            found_key, pid = self.parseDRM(self.sect[drm_ptr:drm_ptr+drm_size], drm_count, goodpids)
            if not found_key:
                raise DrmException("No key found. Most likely the correct PID has not been given.")
            # kill the drm keys
            self.patchSection(0, "\0" * drm_size, drm_ptr)
            # kill the drm pointers
            self.patchSection(0, "\xff" * 4 + "\0" * 12, 0xA8)
            
        if pid=="00000000":
            print "File has default encryption, no specific PID."
        else:
            print "File is encoded with PID "+checksumPid(pid)+"."

        # clear the crypto type
        self.patchSection(0, "\0" * 2, 0xC)

        # decrypt sections
        print "Decrypting. Please wait . . .",
        self.mobi_data = self.data_file[:self.sections[1][0]]
        for i in xrange(1, self.records+1):
            data = self.loadSection(i)
            extra_size = getSizeOfTrailingDataEntries(data, len(data), self.extra_data_flags)
            if i%100 == 0:
                print ".",
            # print "record %d, extra_size %d" %(i,extra_size)
            self.mobi_data += PC1(found_key, data[0:len(data) - extra_size])
            if extra_size > 0:
                self.mobi_data += data[-extra_size:]
        if self.num_sections > self.records+1:
            self.mobi_data += self.data_file[self.sections[self.records+1][0]:]
        print "done"
        return

def getUnencryptedBook(infile,pid):
    if not os.path.isfile(infile):
        raise DrmException('Input File Not Found')
    book = MobiBook(infile)
    book.processBook([pid])
    return book.mobi_data

def getUnencryptedBookWithList(infile,pidlist):
    if not os.path.isfile(infile):
        raise DrmException('Input File Not Found')
    book = MobiBook(infile)
    book.processBook(pidlist)
    return book.mobi_data


def main(argv=sys.argv):
    print ('MobiDeDrm v%(__version__)s. '
	   'Copyright 2008-2010 The Dark Reverser.' % globals())
    if len(argv)<3 or len(argv)>4:
        print "Removes protection from Mobipocket books"
        print "Usage:"
        print "    %s <infile> <outfile> [<Comma separated list of PIDs to try>]" % sys.argv[0]
        return 1
    else:
        infile = argv[1]
        outfile = argv[2]
        if len(argv) is 4:
        	pidlist = argv[3].split(',')
        else:
        	pidlist = {}
        try:
            stripped_file = getUnencryptedBookWithList(infile, pidlist)
            file(outfile, 'wb').write(stripped_file)
        except DrmException, e:
            print "Error: %s" % e
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
